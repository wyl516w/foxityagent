from __future__ import annotations

import ast
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from uuid import uuid4

from agent_studio.core.models import (
    AgentModelAssignment,
    ApprovalTimeoutAction,
    ChatImageAttachment,
    ChatRequest,
    ControlMode,
    ControlActionPayload,
    ControlActionType,
    CreateTaskAgentRequest,
    CreateWorkflowTaskRequest,
    ScriptExecutionPrepareRequest,
    ScriptExecutionRunRequest,
    ScriptRuntime,
    TaskStatus,
    UiStatePayload,
    WorkflowAgentTreeResponse,
    WorkflowRunResponse,
    WorkflowStepDefinition,
    WorkflowStepResult,
    WorkflowStepType,
    WorkflowTaskDetail,
    WorkflowTaskListResponse,
)
from agent_studio.core.state import SharedState
from agent_studio.services.automation.input_controller import InputController
from agent_studio.services.automation.permission_manager import PermissionManager
from agent_studio.services.model_router import ModelRouter
from agent_studio.services.perception.perception_service import PerceptionService
from agent_studio.services.system.system_service import SystemService
from agent_studio.storage.sqlite_store import SQLiteStore


@dataclass
class WorkflowExecutionContext:
    latest_capture_path: str | None = None
    last_ocr_lines: list[str] = field(default_factory=list)
    last_match_coordinates: str | None = None
    last_match_text: str | None = None
    last_analysis: str | None = None
    operator_guidance: list[str] = field(default_factory=list)
    system_info: dict[str, str] = field(default_factory=dict)


@dataclass
class _PendingWorkflowApprovalSignal(Exception):
    agent_id: str
    agent_name: str
    step_index: int
    step: WorkflowStepDefinition
    preview: dict
    context: WorkflowExecutionContext


@dataclass
class _AutonomousPlan:
    status: str
    summary: str
    step: WorkflowStepDefinition | None = None
    delegate_name: str | None = None
    delegate_instruction: str | None = None
    delegate_max_iterations: int = 6
    delegate_model_assignment: AgentModelAssignment | None = None


class WorkflowService:
    def __init__(
        self,
        store: SQLiteStore,
        state: SharedState,
        perception_service: PerceptionService,
        input_controller: InputController,
        permission_manager: PermissionManager,
        system_service: SystemService | None = None,
        model_router: ModelRouter | None = None,
    ) -> None:
        self._store = store
        self._state = state
        self._perception_service = perception_service
        self._input_controller = input_controller
        self._permission_manager = permission_manager
        self._system_service = system_service
        self._model_router = model_router

    def list_tasks(self, conversation_id: str | None = None) -> WorkflowTaskListResponse:
        return WorkflowTaskListResponse(
            tasks=self._store.list_tasks(conversation_id=conversation_id)
        )

    async def list_task_details_for_conversation(
        self,
        conversation_id: str,
    ) -> list[WorkflowTaskDetail]:
        return self._store.list_task_details(conversation_id=conversation_id)

    async def get_task(self, task_id: str) -> WorkflowTaskDetail:
        auto_resolved = await self._auto_resolve_pending_approval(task_id)
        if auto_resolved is not None:
            return auto_resolved
        return self._load_task(task_id)

    def _load_task(self, task_id: str) -> WorkflowTaskDetail:
        task = self._store.get_task(task_id)
        if task is None:
            raise ValueError(f"Task {task_id} was not found.")
        return task

    def create_task(self, request: CreateWorkflowTaskRequest) -> WorkflowTaskDetail:
        task_id = f"task-{uuid4().hex[:12]}"
        title = (request.title or self._derive_title(request.steps, request.instruction)).strip()
        preferred_language = request.preferred_language or "system"
        instruction = request.instruction.strip() or f"Complete the task '{title}'."
        root_agent = self._make_agent_record(
            name=title,
            parent_agent_id=None,
            instruction=instruction,
            preferred_language=preferred_language,
            model_assignment=request.model_assignment,
            steps=request.steps,
            autonomous=request.autonomous,
            max_iterations=request.max_iterations,
        )
        payload = {
            "conversation_id": request.conversation_id,
            "source_message_id": request.source_message_id,
            "source_message_preview": request.source_message_preview,
            "preferred_language": preferred_language,
            "steps": [step.model_dump(mode="json") for step in request.steps],
            "results": [],
            "last_message": "Task created and ready to run.",
            "agents": [root_agent],
        }
        task = self._store.create_task(
            task_id=task_id,
            title=title,
            status=TaskStatus.DRAFT.value,
            payload=payload,
        )
        self._state.append_event(f"Workflow task created: {title} ({task_id}).")
        return task

    def add_agent(
        self,
        task_id: str,
        request: CreateTaskAgentRequest,
    ) -> WorkflowTaskDetail:
        task = self._load_task(task_id)
        payload = self._get_task_payload(task_id=task_id, task=task)
        agents = self._get_agent_records(payload)

        parent_agent_id = request.parent_agent_id
        if parent_agent_id and not any(
            agent.get("agent_id") == parent_agent_id for agent in agents
        ):
            raise ValueError(f"Parent agent {parent_agent_id} was not found.")

        agent_record = self._make_agent_record(
            name=request.name.strip(),
            parent_agent_id=parent_agent_id,
            instruction=request.instruction.strip(),
            preferred_language=request.preferred_language or task.preferred_language,
            model_assignment=request.model_assignment,
            steps=request.steps,
            autonomous=request.autonomous,
            max_iterations=request.max_iterations,
        )
        agents.append(agent_record)
        payload["agents"] = agents
        payload["last_message"] = f"Agent '{request.name.strip()}' added."
        updated_task = self._store.update_task(
            task_id,
            status=task.status.value,
            payload=payload,
            title=task.title,
        )
        self._state.append_event(
            f"Added agent {agent_record['agent_id']} to task {task_id}."
        )
        return updated_task

    def get_agent_tree(self, task_id: str) -> WorkflowAgentTreeResponse:
        task = self._load_task(task_id)
        return WorkflowAgentTreeResponse(
            task_id=task.task_id,
            title=task.title,
            agents=task.agents,
        )

    async def run_task(self, task_id: str) -> WorkflowRunResponse:
        task = self._load_task(task_id)
        payload = self._get_task_payload(task_id=task_id, task=task)
        agents = self._get_agent_records(payload)
        if not agents:
            raise ValueError("This task does not contain any agents to run.")

        for agent in agents:
            agent["status"] = TaskStatus.DRAFT.value
            agent["results"] = []
            agent["last_message"] = None

        payload["results"] = []
        payload["pending_approval"] = None
        payload["execution_context"] = None
        payload["last_message"] = "Task is running."
        self._store.update_task(
            task_id,
            status=TaskStatus.RUNNING.value,
            payload=payload,
            title=task.title,
        )

        context = WorkflowExecutionContext(
            latest_capture_path=self._state.get_ui_state().latest_capture_path
        )
        ordered_agents = self._ordered_agent_records(agents)
        return await self._continue_task_execution(
            task_id=task_id,
            task_title=task.title,
            payload=payload,
            ordered_agents=ordered_agents,
            context=context,
            flat_results=[],
            reset_agents=False,
        )

    async def approve_pending_step(
        self,
        task_id: str,
        *,
        decision: ApprovalTimeoutAction = ApprovalTimeoutAction.ALLOW,
        extra_prompt: str | None = None,
    ) -> WorkflowRunResponse:
        return await self._finalize_pending_step(
            task_id=task_id,
            decision=decision,
            reason=None,
            extra_prompt=extra_prompt,
            automatic=False,
        )

    async def _finalize_pending_step(
        self,
        *,
        task_id: str,
        decision: ApprovalTimeoutAction,
        reason: str | None,
        extra_prompt: str | None,
        automatic: bool,
    ) -> WorkflowRunResponse:
        task = self._load_task(task_id)
        payload = self._get_task_payload(task_id=task_id, task=task)
        pending = payload.get("pending_approval")
        if not isinstance(pending, dict):
            raise ValueError("This task does not have any pending approval request.")

        ordered_agents = self._ordered_agent_records(self._get_agent_records(payload))
        agent_id = str(pending.get("agent_id", ""))
        agent = self._find_agent_record(ordered_agents, agent_id)
        if agent is None:
            raise ValueError(f"Pending approval agent {agent_id} was not found.")

        step_index = int(pending.get("step_index", 0))
        if step_index <= 0:
            raise ValueError("Pending approval step index is invalid.")
        steps = agent.get("steps", [])
        if step_index > len(steps):
            raise ValueError("Pending approval step index is out of range.")
        step = WorkflowStepDefinition.model_validate(steps[step_index - 1])
        context = self._context_from_payload(payload.get("execution_context"))

        if decision == ApprovalTimeoutAction.ALLOW:
            if step.kind == WorkflowStepType.EXECUTE_SCRIPT:
                if self._system_service is None:
                    raise ValueError("System service is unavailable for script approvals.")
                execution = self._system_service.execute_prepared_script(
                    ScriptExecutionRunRequest(
                        confirmation_id=str(pending.get("confirmation_id", "")),
                        confirm=True,
                    )
                )
                result = WorkflowStepResult(
                    index=step_index,
                    kind=step.kind,
                    agent_id=agent_id,
                    agent_name=str(agent.get("name", "Agent")),
                    label=step.label,
                    ok=execution.ok,
                    message=execution.summary,
                    output={
                        "runtime": execution.runtime.value,
                        "preferred_shell": execution.preferred_shell,
                        "exit_code": execution.exit_code,
                        "timed_out": execution.timed_out,
                        "stdout": execution.stdout,
                        "stderr": execution.stderr,
                        "auto_decision": automatic,
                    },
                )
            elif step.kind in {
                WorkflowStepType.MOVE_MOUSE,
                WorkflowStepType.LEFT_CLICK,
                WorkflowStepType.TYPE_TEXT,
            }:
                action_payload = self._control_action_payload(step=step, context=context)
                self._permission_manager.approve_once(action_payload.action.value)
                control_result = self._input_controller.execute(action_payload)
                result = WorkflowStepResult(
                    index=step_index,
                    kind=step.kind,
                    agent_id=agent_id,
                    agent_name=str(agent.get("name", "Agent")),
                    label=step.label,
                    ok=control_result.allowed
                    and (
                        control_result.executed
                        or "noop" in control_result.message.lower()
                    ),
                    message=control_result.message,
                    output={
                        "event": control_result.event,
                        "auto_decision": automatic,
                        "coordinates": action_payload.text
                        if action_payload.action == ControlActionType.MOVE_MOUSE
                        else None,
                        "text_length": len(action_payload.text or "")
                        if action_payload.action == ControlActionType.TYPE_TEXT
                        else None,
                    },
                )
            else:
                raise ValueError(
                    f"Inline approval is not supported for workflow step {step.kind.value}."
                )
        elif decision == ApprovalTimeoutAction.DENY:
            result = WorkflowStepResult(
                index=step_index,
                kind=step.kind,
                agent_id=agent_id,
                agent_name=str(agent.get("name", "Agent")),
                label=step.label,
                ok=False,
                message=reason or "High-risk step was denied.",
                output={
                    "confirmation_id": str(pending.get("confirmation_id", "")),
                    "auto_decision": automatic,
                    "denied": True,
                },
            )
        else:
            guidance = (extra_prompt or reason or "").strip()
            if not guidance:
                raise ValueError(
                    "Prompt guidance is required when resolving approval with extra guidance."
                )
            context.operator_guidance.append(guidance)
            result = WorkflowStepResult(
                index=step_index,
                kind=step.kind,
                agent_id=agent_id,
                agent_name=str(agent.get("name", "Agent")),
                label=step.label,
                ok=True,
                message=(
                    reason
                    or "Additional operator guidance was recorded instead of running the high-risk step."
                ),
                output={
                    "confirmation_id": str(pending.get("confirmation_id", "")),
                    "auto_decision": automatic,
                    "prompted": True,
                    "guidance": guidance,
                    "script_skipped": True,
                },
            )
        agent.setdefault("results", []).append(result.model_dump(mode="json"))
        flat_results = [
            WorkflowStepResult.model_validate(item) for item in payload.get("results", [])
        ]
        flat_results.append(result)
        payload["results"] = [item.model_dump(mode="json") for item in flat_results]
        payload["pending_approval"] = None
        payload["last_message"] = result.message

        if decision == ApprovalTimeoutAction.DENY and not step.continue_on_error:
            self._mark_agent_path_status(
                agents=ordered_agents,
                agent_id=agent_id,
                status=TaskStatus.FAILED,
                message=result.message,
            )
            payload["agents"] = ordered_agents
            payload["execution_context"] = self._context_to_payload(context)
            updated_task = self._store.update_task(
                task_id,
                status=TaskStatus.FAILED.value,
                payload=payload,
                title=task.title,
            )
            return WorkflowRunResponse(task=updated_task)

        return await self._continue_task_execution(
            task_id=task_id,
            task_title=task.title,
            payload=payload,
            ordered_agents=ordered_agents,
            context=context,
            flat_results=flat_results,
            reset_agents=False,
            resume=True,
        )

    async def _auto_resolve_pending_approval(
        self,
        task_id: str,
    ) -> WorkflowTaskDetail | None:
        task = self._store.get_task(task_id)
        if task is None:
            raise ValueError(f"Task {task_id} was not found.")
        payload = self._store.get_task_payload(task_id) or {}
        pending = payload.get("pending_approval")
        if not isinstance(pending, dict):
            return None

        expires_at_raw = pending.get("expires_at")
        if not isinstance(expires_at_raw, str):
            return None
        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
        except ValueError:
            return None
        if datetime.now(timezone.utc) < expires_at:
            return None

        action = ApprovalTimeoutAction(
            str(
                pending.get(
                    "timeout_action",
                    ApprovalTimeoutAction.DENY.value,
                )
            )
        )
        timeout_seconds = int(pending.get("approval_timeout_seconds", 60))
        if action == ApprovalTimeoutAction.ALLOW:
            resolved = await self._finalize_pending_step(
                task_id=task_id,
                decision=ApprovalTimeoutAction.ALLOW,
                reason=(
                    f"Approval timed out after {timeout_seconds} seconds and defaulted to allow."
                ),
                extra_prompt=None,
                automatic=True,
            )
        elif action == ApprovalTimeoutAction.PROMPT:
            resolved = await self._finalize_pending_step(
                task_id=task_id,
                decision=ApprovalTimeoutAction.PROMPT,
                reason=(
                    f"Approval timed out after {timeout_seconds} seconds and continued with fallback guidance."
                ),
                extra_prompt=str(
                    pending.get(
                        "timeout_prompt",
                        self._state.get_automation_settings().approval_timeout_prompt,
                    )
                ),
                automatic=True,
            )
        else:
            resolved = await self._finalize_pending_step(
                task_id=task_id,
                decision=ApprovalTimeoutAction.DENY,
                reason=(
                    f"Approval timed out after {timeout_seconds} seconds and defaulted to deny."
                ),
                extra_prompt=None,
                automatic=True,
            )
        self._state.append_event(
            f"Pending approval for task {task_id} auto-resolved with {action.value}."
        )
        return resolved.task

    async def _continue_task_execution(
        self,
        *,
        task_id: str,
        task_title: str,
        payload: dict,
        ordered_agents: list[dict],
        context: WorkflowExecutionContext,
        flat_results: list[WorkflowStepResult],
        reset_agents: bool = False,
        resume: bool = False,
    ) -> WorkflowRunResponse:
        if reset_agents:
            for agent in ordered_agents:
                agent["status"] = TaskStatus.DRAFT.value
                agent["results"] = []
                agent["last_message"] = None

        roots = [agent for agent in ordered_agents if not agent.get("parent_agent_id")]
        final_status = TaskStatus.COMPLETED

        try:
            for root in roots:
                subtree_status = await self._run_agent_subtree(
                    agent=root,
                    all_agents=ordered_agents,
                    context=context,
                    flat_results=flat_results,
                    resume=resume,
                )
                if subtree_status == TaskStatus.WAITING_APPROVAL:
                    final_status = TaskStatus.WAITING_APPROVAL
                    break
                if subtree_status == TaskStatus.FAILED:
                    final_status = TaskStatus.FAILED
        except _PendingWorkflowApprovalSignal as pending:
            final_status = TaskStatus.WAITING_APPROVAL
            self._mark_agent_path_status(
                agents=ordered_agents,
                agent_id=pending.agent_id,
                status=TaskStatus.WAITING_APPROVAL,
                message=str(pending.preview.get("summary", "Waiting for approval.")),
            )
            automation_settings = self._state.get_automation_settings()
            created_at = datetime.now(timezone.utc)
            expires_at = created_at + timedelta(
                seconds=automation_settings.approval_timeout_seconds
            )
            payload["pending_approval"] = {
                "agent_id": pending.agent_id,
                "agent_name": pending.agent_name,
                "step_index": pending.step_index,
                "confirmation_id": pending.preview.get("confirmation_id"),
                "runtime": pending.preview.get("runtime"),
                "risk_level": pending.preview.get("risk_level"),
                "created_at": created_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "approval_timeout_seconds": automation_settings.approval_timeout_seconds,
                "timeout_action": automation_settings.approval_timeout_action.value,
                "timeout_prompt": automation_settings.approval_timeout_prompt,
                "summary": pending.preview.get("summary"),
                "warnings": pending.preview.get("warnings", []),
                "preview": pending.preview.get("preview", ""),
            }
            payload["last_message"] = str(
                pending.preview.get("summary", "Task is waiting for approval.")
            )

        payload["agents"] = ordered_agents
        payload["results"] = [result.model_dump(mode="json") for result in flat_results]
        payload["steps"] = ordered_agents[0].get("steps", payload.get("steps", []))
        payload["execution_context"] = self._context_to_payload(context)
        if final_status != TaskStatus.WAITING_APPROVAL:
            payload["pending_approval"] = None
            if flat_results:
                payload["last_message"] = flat_results[-1].message
        if not payload.get("last_message"):
            payload["last_message"] = (
                flat_results[-1].message if flat_results else "Task finished without actions."
            )

        updated_task = self._store.update_task(
            task_id,
            status=final_status.value,
            payload=payload,
            title=task_title,
        )
        self._state.append_event(
            f"Workflow task {updated_task.task_id} finished with status {updated_task.status.value}."
        )
        return WorkflowRunResponse(task=updated_task)

    async def _run_agent_subtree(
        self,
        *,
        agent: dict,
        all_agents: list[dict],
        context: WorkflowExecutionContext,
        flat_results: list[WorkflowStepResult],
        resume: bool = False,
    ) -> TaskStatus:
        if resume and agent.get("status") == TaskStatus.COMPLETED.value:
            return TaskStatus.COMPLETED

        if not resume:
            agent["results"] = []
            agent["autonomous_turns"] = 0
            agent["autonomous_complete"] = False
        agent["status"] = TaskStatus.RUNNING.value
        child_statuses: list[TaskStatus] = []
        if resume:
            agent["autonomous_turns"] = int(agent.get("autonomous_turns", 0))
            agent["autonomous_complete"] = bool(agent.get("autonomous_complete", False))

        while True:
            completed_steps = len(agent.get("results", [])) if resume else len(agent["results"])
            steps = list(agent.get("steps", []))
            progressed = False

            for index, raw_step in enumerate(steps, start=1):
                if index <= completed_steps:
                    continue
                step = WorkflowStepDefinition.model_validate(raw_step)
                result = await self._execute_step(
                    index=index,
                    step=step,
                    context=context,
                    agent_id=str(agent["agent_id"]),
                    agent_name=str(agent["name"]),
                    model_settings=self._model_settings_for_agent(agent),
                )
                agent["results"].append(result.model_dump(mode="json"))
                flat_results.append(result)
                progressed = True
                if not result.ok and not step.continue_on_error:
                    agent["status"] = TaskStatus.FAILED.value
                    agent["last_message"] = result.message
                    return TaskStatus.FAILED

            resume = False
            if not agent.get("autonomous") or agent.get("autonomous_complete"):
                break

            if int(agent.get("autonomous_turns", 0)) >= int(agent.get("max_iterations", 8)):
                message = "Autonomous planning reached the maximum number of iterations."
                agent["status"] = TaskStatus.FAILED.value
                agent["last_message"] = message
                failure_result = WorkflowStepResult(
                    index=len(agent.get("steps", [])) + 1,
                    kind=WorkflowStepType.COMPLETE,
                    agent_id=str(agent["agent_id"]),
                    agent_name=str(agent["name"]),
                    ok=False,
                    message=message,
                )
                agent["results"].append(failure_result.model_dump(mode="json"))
                flat_results.append(failure_result)
                return TaskStatus.FAILED

            try:
                plan = await self._decide_autonomous_action(
                    agent=agent,
                    context=context,
                    recent_results=agent.get("results", []),
                )
            except Exception as exc:
                message = f"Autonomous planning failed: {exc}"
                self._state.append_event(
                    f"Autonomous planning failed for agent {agent.get('name', 'Agent')}: {exc}"
                )
                failure_result = WorkflowStepResult(
                    index=len(agent.get("steps", [])) + 1,
                    kind=WorkflowStepType.COMPLETE,
                    agent_id=str(agent["agent_id"]),
                    agent_name=str(agent["name"]),
                    ok=False,
                    message=message,
                )
                agent["results"].append(failure_result.model_dump(mode="json"))
                flat_results.append(failure_result)
                agent["status"] = TaskStatus.FAILED.value
                agent["last_message"] = message
                return TaskStatus.FAILED
            agent["autonomous_turns"] = int(agent.get("autonomous_turns", 0)) + 1
            if plan.status == "continue" and plan.step is not None:
                agent.setdefault("steps", []).append(plan.step.model_dump(mode="json"))
                progressed = True
                continue
            if plan.status == "delegate":
                child_name = (plan.delegate_name or "Subagent").strip()
                child_instruction = (plan.delegate_instruction or "").strip()
                normalized_child_instruction = child_instruction.lower()
                delegated_instructions = {
                    str((item.get("output") or {}).get("child_instruction") or "")
                    .strip()
                    .lower()
                    for item in agent.get("results", [])
                    if str(item.get("kind", "")) == WorkflowStepType.DELEGATE_AGENT.value
                }
                if (
                    normalized_child_instruction
                    and normalized_child_instruction in delegated_instructions
                ):
                    agent["autonomous_complete"] = True
                    completion_step = WorkflowStepDefinition(
                        kind=WorkflowStepType.COMPLETE,
                        label="Complete",
                    )
                    agent.setdefault("steps", []).append(
                        completion_step.model_dump(mode="json")
                    )
                    completion_result = WorkflowStepResult(
                        index=len(agent.get("steps", [])),
                        kind=WorkflowStepType.COMPLETE,
                        agent_id=str(agent["agent_id"]),
                        agent_name=str(agent["name"]),
                        label=completion_step.label,
                        ok=True,
                        message=(
                            plan.summary
                            or "Repeated delegation request detected; finishing with current findings."
                        ),
                    )
                    agent["results"].append(completion_result.model_dump(mode="json"))
                    flat_results.append(completion_result)
                    break
                child_agent = self._make_agent_record(
                    name=child_name,
                    parent_agent_id=str(agent["agent_id"]),
                    instruction=child_instruction,
                    preferred_language=str(
                        agent.get("preferred_language")
                        or self._state.get_ui_state().language
                        or "system"
                    ),
                    model_assignment=plan.delegate_model_assignment,
                    steps=[],
                    autonomous=True,
                    max_iterations=plan.delegate_max_iterations,
                )
                all_agents.append(child_agent)
                synthetic_step = WorkflowStepDefinition(
                    kind=WorkflowStepType.DELEGATE_AGENT,
                    label="Delegate Agent",
                    text=child_instruction,
                )
                agent.setdefault("steps", []).append(synthetic_step.model_dump(mode="json"))
                delegate_result = WorkflowStepResult(
                    index=len(agent.get("steps", [])),
                    kind=WorkflowStepType.DELEGATE_AGENT,
                    agent_id=str(agent["agent_id"]),
                    agent_name=str(agent["name"]),
                    label=synthetic_step.label,
                    ok=True,
                    message=plan.summary or f"Delegated to {child_name}.",
                    output={
                        "child_agent_id": child_agent["agent_id"],
                        "child_name": child_name,
                        "child_instruction": child_instruction,
                        "child_max_iterations": plan.delegate_max_iterations,
                        "model_assignment": (
                            plan.delegate_model_assignment.model_dump(mode="json")
                            if plan.delegate_model_assignment is not None
                            else None
                        ),
                    },
                )
                agent["results"].append(delegate_result.model_dump(mode="json"))
                flat_results.append(delegate_result)
                progressed = True
                continue

            agent["autonomous_complete"] = True
            completion_step = WorkflowStepDefinition(
                kind=WorkflowStepType.COMPLETE,
                label="Complete",
            )
            agent.setdefault("steps", []).append(completion_step.model_dump(mode="json"))
            completion_result = WorkflowStepResult(
                index=len(agent.get("steps", [])),
                kind=WorkflowStepType.COMPLETE,
                agent_id=str(agent["agent_id"]),
                agent_name=str(agent["name"]),
                label=completion_step.label,
                ok=True,
                message=plan.summary or "Agent marked the task complete.",
            )
            agent["results"].append(completion_result.model_dump(mode="json"))
            flat_results.append(completion_result)
            break

            if not progressed:
                break

        for child in self._child_agents(parent_agent_id=str(agent["agent_id"]), agents=all_agents):
            child_status = await self._run_agent_subtree(
                agent=child,
                all_agents=all_agents,
                context=context,
                flat_results=flat_results,
                resume=resume,
            )
            child_statuses.append(child_status)

        if any(status == TaskStatus.WAITING_APPROVAL for status in child_statuses):
            final_status = TaskStatus.WAITING_APPROVAL
        elif any(status == TaskStatus.FAILED for status in child_statuses):
            final_status = TaskStatus.FAILED
        else:
            final_status = TaskStatus.COMPLETED
        agent["status"] = final_status.value
        if agent["results"]:
            agent["last_message"] = agent["results"][-1].get("message")
        elif child_statuses:
            child_messages = [
                child.get("last_message")
                for child in self._child_agents(parent_agent_id=str(agent["agent_id"]), agents=all_agents)
                if child.get("last_message")
            ]
            agent["last_message"] = child_messages[-1] if child_messages else None
        else:
            agent["last_message"] = "Agent finished without local steps."
        return final_status

    async def _execute_step(
        self,
        *,
        index: int,
        step: WorkflowStepDefinition,
        context: WorkflowExecutionContext,
        agent_id: str,
        agent_name: str,
        model_settings=None,
    ) -> WorkflowStepResult:
        label = step.label
        try:
            if step.kind == WorkflowStepType.DETECT_SYSTEM:
                if self._system_service is None:
                    raise ValueError("System service is unavailable for detect_system steps.")
                system_info = self._system_service.get_system_info()
                context.system_info = system_info.model_dump(mode="json")
                return WorkflowStepResult(
                    index=index,
                    kind=step.kind,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    label=label,
                    ok=True,
                    message=(
                        f"Detected {system_info.os_name} {system_info.os_release} "
                        f"on {system_info.machine}."
                    ),
                    output=context.system_info,
                )

            if step.kind == WorkflowStepType.CAPTURE_SCREEN:
                capture = self._perception_service.capture_screen()
                if capture.ok and capture.image_path:
                    context.latest_capture_path = capture.image_path
                    self._state.update_ui_state(
                        UiStatePayload(latest_capture_path=capture.image_path)
                    )
                return WorkflowStepResult(
                    index=index,
                    kind=step.kind,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    label=label,
                    ok=capture.ok,
                    message=capture.message,
                    output={
                        "image_path": capture.image_path,
                        "width": capture.width,
                        "height": capture.height,
                    },
                )

            if step.kind == WorkflowStepType.RUN_OCR:
                image_path = self._resolve_image_path(step=step, context=context)
                context.latest_capture_path = image_path
                ocr = self._perception_service.run_ocr(image_path)
                context.last_ocr_lines = [line.text for line in ocr.lines]
                return WorkflowStepResult(
                    index=index,
                    kind=step.kind,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    label=label,
                    ok=ocr.ok,
                    message=ocr.message,
                    output={
                        "image_path": ocr.image_path,
                        "engine": ocr.engine,
                        "lines": [line.model_dump(mode="json") for line in ocr.lines],
                    },
                )

            if step.kind == WorkflowStepType.FIND_TEXT:
                image_path = self._resolve_image_path(step=step, context=context)
                context.latest_capture_path = image_path
                query = (step.text or "").strip()
                if not query:
                    raise ValueError("find_text steps require step.text to contain the query.")
                lookup = self._perception_service.find_text(
                    image_path=image_path,
                    query=query,
                    case_sensitive=step.case_sensitive,
                )
                if lookup.matches:
                    first_match = lookup.matches[0]
                    context.last_match_coordinates = (
                        f"{first_match.center_x},{first_match.center_y}"
                    )
                    context.last_match_text = first_match.text
                return WorkflowStepResult(
                    index=index,
                    kind=step.kind,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    label=label,
                    ok=lookup.ok,
                    message=lookup.message,
                    output={
                        "image_path": lookup.image_path,
                        "query": lookup.query,
                        "matches": [
                            match.model_dump(mode="json") for match in lookup.matches
                        ],
                    },
                )

            if step.kind == WorkflowStepType.ANALYZE_IMAGE:
                if self._model_router is None:
                    raise ValueError("Model router is unavailable for analyze_image steps.")
                image_path = self._resolve_image_path(step=step, context=context)
                context.latest_capture_path = image_path
                prompt = (
                    (step.text or "").strip()
                    or "Describe the image and highlight actionable UI details."
                )
                response = await self._model_router.chat(
                    ChatRequest(
                        message=_build_analysis_request(
                            prompt,
                            operator_guidance=context.operator_guidance,
                        ),
                        attachments=[
                            ChatImageAttachment(
                                name=Path(image_path).name,
                                image_path=image_path,
                            )
                        ],
                        system_prompt=(
                            "You are a desktop automation vision analyst. "
                            "You produce concise visual summaries and practical next desktop actions."
                        ),
                    ),
                    settings_override=model_settings,
                )
                self._state.append_event(
                    "Analyze image sent screenshot "
                    f"{image_path} to {response.provider.value}/{response.model} "
                    f"with {response.attachment_count} attachment(s)."
                )
                analysis = _parse_analysis_response(
                    response.content,
                    fallback_image_path=image_path,
                )
                context.last_analysis = analysis["summary"]
                summarized = analysis["summary"].strip()
                if len(summarized) > 220:
                    summarized = summarized[:217].rstrip() + "..."
                return WorkflowStepResult(
                    index=index,
                    kind=step.kind,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    label=label,
                    ok=True,
                    message=summarized or "Image analysis completed.",
                    output={
                        "image_path": image_path,
                        "provider": response.provider.value,
                        "model": response.model,
                        "content": analysis["summary"],
                        "raw_content": response.content,
                        "suggested_steps": analysis["suggested_steps"],
                        "vision_used": response.vision_used,
                        "attachment_count": response.attachment_count,
                    },
                )

            if step.kind == WorkflowStepType.EXECUTE_SCRIPT:
                if self._system_service is None:
                    raise ValueError("System service is unavailable for execute_script steps.")
                script = (step.text or "").strip()
                if not script:
                    raise ValueError("execute_script steps require step.text to contain the script.")
                preview = await self._system_service.prepare_script_execution(
                    ScriptExecutionPrepareRequest(
                        script=script,
                        runtime=step.runtime or ScriptRuntime.AUTO,
                        timeout_seconds=30.0,
                        approval_timeout_seconds=(
                            self._state.get_automation_settings().approval_timeout_seconds
                        ),
                    )
                )
                raise _PendingWorkflowApprovalSignal(
                    agent_id=agent_id,
                    agent_name=agent_name,
                    step_index=index,
                    step=step,
                    preview=preview.model_dump(mode="json"),
                    context=context,
                )

            if step.kind == WorkflowStepType.MOVE_MOUSE:
                return self._run_control_step(
                    index=index,
                    step=step,
                    context=context,
                    agent_id=agent_id,
                    agent_name=agent_name,
                )

            if step.kind == WorkflowStepType.LEFT_CLICK:
                return self._run_control_step(
                    index=index,
                    step=step,
                    context=context,
                    agent_id=agent_id,
                    agent_name=agent_name,
                )

            if step.kind == WorkflowStepType.TYPE_TEXT:
                return self._run_control_step(
                    index=index,
                    step=step,
                    context=context,
                    agent_id=agent_id,
                    agent_name=agent_name,
                )

            raise ValueError(f"Unsupported workflow step: {step.kind}")
        except _PendingWorkflowApprovalSignal:
            raise
        except Exception as exc:
            self._state.append_event(
                f"Workflow step {index} ({step.kind.value}) failed for agent {agent_name}: {exc}"
            )
            return WorkflowStepResult(
                index=index,
                kind=step.kind,
                agent_id=agent_id,
                agent_name=agent_name,
                label=label,
                ok=False,
                message=str(exc),
            )

    async def _decide_autonomous_action(
        self,
        *,
        agent: dict,
        context: WorkflowExecutionContext,
        recent_results: list[dict],
    ) -> _AutonomousPlan:
        if self._model_router is None:
            raise ValueError("Model router is unavailable for autonomous agents.")
        response = await self._model_router.chat(
            ChatRequest(
                message=_build_autonomous_request(
                    agent=agent,
                    context=context,
                    recent_results=recent_results,
                    model_settings=self._model_settings_for_agent(agent),
                    provider_options=self._provider_option_lines(agent),
                ),
                system_prompt=(
                    "You are an autonomous desktop agent planner. "
                    "You must return strict JSON only, select exactly one next action, "
                    "and never ask the user to manually enumerate steps."
                ),
            ),
            settings_override=self._model_settings_for_agent(agent),
        )
        try:
            return _parse_autonomous_plan(
                response.content,
                default_iterations=int(agent.get("max_iterations", 8)),
            )
        except ValueError as exc:
            repair_response = await self._model_router.chat(
                ChatRequest(
                    message=_build_autonomous_repair_request(response.content),
                    system_prompt=(
                        "You repair autonomous planner output into strict JSON only. "
                        "Do not add markdown, explanations, or extra prose."
                    ),
                ),
                settings_override=self._model_settings_for_agent(agent),
            )
            try:
                return _parse_autonomous_plan(
                    repair_response.content,
                    default_iterations=int(agent.get("max_iterations", 8)),
                )
            except ValueError:
                inferred = _infer_autonomous_plan(
                    repair_response.content or response.content,
                    default_iterations=int(agent.get("max_iterations", 8)),
                )
                if inferred is not None:
                    self._state.append_event(
                        f"Autonomous planner output for agent {agent.get('name', 'Agent')} was repaired heuristically."
                    )
                    return inferred
                raise exc

    def _get_task_payload(self, *, task_id: str, task: WorkflowTaskDetail) -> dict:
        payload = self._store.get_task_payload(task_id)
        if payload is None:
            raise ValueError(f"Task {task_id} was not found.")
        if payload.get("agents"):
            return payload

        root_agent = self._make_agent_record(
            name=task.title,
            parent_agent_id=None,
            instruction=f"Complete the task '{task.title}'.",
            preferred_language=task.preferred_language,
            model_assignment=None,
            steps=task.steps,
            autonomous=not bool(task.steps),
            max_iterations=8,
        )
        root_agent["status"] = task.status.value
        root_agent["results"] = [result.model_dump(mode="json") for result in task.results]
        root_agent["last_message"] = task.last_message
        payload["agents"] = [root_agent]
        self._store.update_task(
            task_id,
            status=task.status.value,
            payload=payload,
            title=task.title,
        )
        return payload

    @staticmethod
    def _get_agent_records(payload: dict) -> list[dict]:
        records = payload.get("agents", [])
        if not isinstance(records, list):
            return []
        normalized: list[dict] = []
        for record in records:
            if isinstance(record, dict) and record.get("agent_id"):
                normalized.append(dict(record))
        return normalized

    @staticmethod
    def _ordered_agent_records(records: list[dict]) -> list[dict]:
        return [dict(record) for record in records]

    @staticmethod
    def _child_agents(*, parent_agent_id: str, agents: list[dict]) -> list[dict]:
        return [
            agent for agent in agents if str(agent.get("parent_agent_id") or "") == parent_agent_id
        ]

    @staticmethod
    def _make_agent_record(
        *,
        name: str,
        parent_agent_id: str | None,
        instruction: str,
        preferred_language: str,
        model_assignment: AgentModelAssignment | None,
        steps: list[WorkflowStepDefinition],
        autonomous: bool,
        max_iterations: int,
    ) -> dict:
        return {
            "agent_id": f"agent-{uuid4().hex[:12]}",
            "parent_agent_id": parent_agent_id,
            "name": name,
            "instruction": instruction,
            "preferred_language": preferred_language or "system",
            "model_assignment": (
                model_assignment.model_dump(mode="json")
                if model_assignment is not None
                else None
            ),
            "autonomous": autonomous,
            "max_iterations": max(1, int(max_iterations)),
            "autonomous_turns": 0,
            "autonomous_complete": False,
            "status": TaskStatus.DRAFT.value,
            "steps": [step.model_dump(mode="json") for step in steps],
            "results": [],
            "last_message": None,
        }

    def _model_settings_for_agent(self, agent: dict):
        if self._model_router is None:
            return None
        return self._model_router.resolve_settings(assignment=agent.get("model_assignment"))

    def _provider_option_lines(self, agent: dict) -> list[str]:
        if self._model_router is None:
            return []
        capabilities = self._model_router.describe_capabilities(
            self._model_settings_for_agent(agent)
        ).capabilities
        return [
            (
                f"- {profile.provider.value}: default={profile.default_model}; "
                f"vision={'yes' if profile.supports_vision else 'no'}; "
                f"local={'yes' if profile.local_runtime else 'no'}"
            )
            for profile in capabilities
        ]

    @staticmethod
    def _find_agent_record(records: list[dict], agent_id: str) -> dict | None:
        for record in records:
            if str(record.get("agent_id", "")) == agent_id:
                return record
        return None

    def _mark_agent_path_status(
        self,
        *,
        agents: list[dict],
        agent_id: str,
        status: TaskStatus,
        message: str,
    ) -> None:
        current = self._find_agent_record(agents, agent_id)
        while current is not None:
            current["status"] = status.value
            current["last_message"] = message
            parent_id = current.get("parent_agent_id")
            if not parent_id:
                break
            current = self._find_agent_record(agents, str(parent_id))

    @staticmethod
    def _context_to_payload(context: WorkflowExecutionContext) -> dict:
        return {
            "latest_capture_path": context.latest_capture_path,
            "last_ocr_lines": list(context.last_ocr_lines),
            "last_match_coordinates": context.last_match_coordinates,
            "last_match_text": context.last_match_text,
            "last_analysis": context.last_analysis,
            "operator_guidance": list(context.operator_guidance),
            "system_info": dict(context.system_info),
        }

    @staticmethod
    def _context_from_payload(payload: dict | None) -> WorkflowExecutionContext:
        if not isinstance(payload, dict):
            return WorkflowExecutionContext()
        return WorkflowExecutionContext(
            latest_capture_path=payload.get("latest_capture_path"),
            last_ocr_lines=list(payload.get("last_ocr_lines", [])),
            last_match_coordinates=payload.get("last_match_coordinates"),
            last_match_text=payload.get("last_match_text"),
            last_analysis=payload.get("last_analysis"),
            operator_guidance=list(payload.get("operator_guidance", [])),
            system_info=dict(payload.get("system_info", {})),
        )

    @staticmethod
    def _derive_title(
        steps: list[WorkflowStepDefinition],
        instruction: str = "",
    ) -> str:
        if steps:
            first = steps[0]
            if first.label:
                return first.label
            return f"{first.kind.value.replace('_', ' ').title()} Workflow"
        normalized = " ".join(instruction.split()).strip()
        if not normalized:
            return "Autonomous Workflow"
        return normalized[:48].rstrip() if len(normalized) > 48 else normalized

    def _run_control_step(
        self,
        *,
        index: int,
        step: WorkflowStepDefinition,
        context: WorkflowExecutionContext,
        agent_id: str,
        agent_name: str,
    ) -> WorkflowStepResult:
        payload = self._control_action_payload(step=step, context=context)
        result = self._input_controller.execute(payload)
        if (
            not result.allowed
            and self._state.get_automation_settings().control_mode == ControlMode.ASK
        ):
            raise _PendingWorkflowApprovalSignal(
                agent_id=agent_id,
                agent_name=agent_name,
                step_index=index,
                step=step,
                preview={
                    "summary": (
                        "Desktop control is waiting for inline approval before continuing."
                    ),
                    "risk_level": "high",
                    "warnings": [
                        "The current desktop control mode is set to ask every time.",
                        f"Action: {payload.action.value}",
                    ],
                    "preview": payload.text or payload.action.value,
                },
                context=context,
            )
        output: dict[str, object] = {"event": result.event}
        if payload.action == ControlActionType.MOVE_MOUSE:
            output["coordinates"] = payload.text
        if payload.action == ControlActionType.TYPE_TEXT:
            output["text_length"] = len(payload.text or "")
        return WorkflowStepResult(
            index=index,
            kind=step.kind,
            agent_id=agent_id,
            agent_name=agent_name,
            label=step.label,
            ok=result.allowed and (result.executed or "noop" in result.message.lower()),
            message=result.message,
            output=output,
        )

    @staticmethod
    def _control_action_payload(
        *,
        step: WorkflowStepDefinition,
        context: WorkflowExecutionContext,
    ) -> ControlActionPayload:
        if step.kind == WorkflowStepType.MOVE_MOUSE:
            coordinates = (
                (step.text or "").strip()
                or (context.last_match_coordinates or "").strip()
                or "80,80"
            )
            return ControlActionPayload(
                action=ControlActionType.MOVE_MOUSE,
                text=coordinates,
            )
        if step.kind == WorkflowStepType.LEFT_CLICK:
            return ControlActionPayload(action=ControlActionType.LEFT_CLICK)
        if step.kind == WorkflowStepType.TYPE_TEXT:
            text = (step.text or "").strip()
            if not text:
                raise ValueError("type_text steps require step.text to contain content.")
            return ControlActionPayload(
                action=ControlActionType.TYPE_TEXT,
                text=text,
            )
        raise ValueError(f"Unsupported control step: {step.kind.value}")

    @staticmethod
    def _resolve_image_path(
        *,
        step: WorkflowStepDefinition,
        context: WorkflowExecutionContext,
    ) -> str:
        if step.image_path:
            return str(Path(step.image_path))
        if context.latest_capture_path:
            return context.latest_capture_path
        raise ValueError(
            "This step requires an image, but no image_path was provided and no capture is available yet."
        )


def _build_autonomous_request(
    *,
    agent: dict,
    context: WorkflowExecutionContext,
    recent_results: list[dict],
    model_settings=None,
    provider_options: list[str] | None = None,
) -> str:
    instruction = str(agent.get("instruction") or agent.get("name") or "").strip()
    steps = list(agent.get("steps", []))
    turns_used = int(agent.get("autonomous_turns", 0))
    turns_total = int(agent.get("max_iterations", 8))
    lines = [
        f"Agent name: {agent.get('name', 'Agent')}",
        f"Goal: {instruction}",
        f"Planning turns remaining: {max(0, turns_total - turns_used)} / {turns_total}",
        "",
        "Current context:",
        f"- Latest capture: {context.latest_capture_path or 'none'}",
        f"- Last OCR lines: {', '.join(context.last_ocr_lines[:3]) or 'none'}",
        f"- Last text match: {context.last_match_text or 'none'}",
        f"- Last coordinates: {context.last_match_coordinates or 'none'}",
        f"- Last image analysis: {(context.last_analysis or 'none')[:240]}",
        f"- System info: {json.dumps(context.system_info, ensure_ascii=True) if context.system_info else 'none'}",
        (
            "- Current model route: "
            f"{getattr(model_settings, 'provider', None).value if getattr(model_settings, 'provider', None) else 'inherit'} / "
            f"{getattr(model_settings, 'model', None) or 'inherit'}"
        ),
    ]
    base_url = getattr(model_settings, "base_url", "") or ""
    if base_url:
        lines.append(f"- Current base URL: {base_url}")
    if context.operator_guidance:
        lines.extend(
            [
                "",
                "Operator guidance:",
                *[f"- {item}" for item in context.operator_guidance[-5:] if item.strip()],
            ]
        )
    if steps:
        lines.extend(["", "Planned steps so far:"])
        for index, step in enumerate(steps[-8:], start=max(1, len(steps) - 7)):
            lines.append(
                f"- {index}. {step.get('kind', 'unknown')} :: {step.get('text') or ''}".rstrip()
            )
    if recent_results:
        lines.extend(["", "Recent results:"])
        for result in recent_results[-8:]:
            lines.append(
                f"- {result.get('kind', 'unknown')} -> {result.get('message', '')}"
            )
    if provider_options:
        lines.extend(["", "Available provider routes:"] + provider_options)
    lines.extend(
        [
            "",
            "Return JSON only and choose exactly one next action.",
            (
                'Use one of these shapes: '
                '{"status":"continue","summary":"...","action":{"kind":"capture_screen"}} '
                'or {"status":"delegate","summary":"...","delegate":{"name":"Verifier","instruction":"...",'
                '"max_iterations":4,"provider":"ollama","model":"qwen3-vl:4b","base_url":"http://127.0.0.1:11434","assignment_reason":"Use local vision"}} '
                'or {"status":"complete","summary":"..."}'
            ),
            (
                "Valid action kinds: detect_system, capture_screen, run_ocr, find_text, "
                "analyze_image, execute_script, move_mouse, left_click, type_text."
            ),
            "For find_text, type_text, and execute_script, provide action.text.",
            "For run_ocr, find_text, or analyze_image, you may provide action.image_path.",
            (
                "When visual context is needed, always prefer capture_screen then analyze_image "
                "so the latest screenshot is sent to the model as an image attachment."
            ),
            (
                "Use run_ocr only when the task explicitly requires exact OCR-style text extraction."
            ),
            "For execute_script, you may provide action.runtime as auto, python, or shell.",
            "Do not ask the operator to break the goal into steps for you; choose the next tool yourself.",
            "Use delegate only when a child agent should take over a clear sub-goal.",
            "Delegate payloads may optionally set provider, model, base_url, and assignment_reason.",
            "Use complete when the goal has been satisfied or no safe next action remains.",
        ]
    )
    return "\n".join(lines)


def _build_autonomous_repair_request(content: str) -> str:
    return "\n".join(
        [
            "Convert the planner output below into one strict JSON object.",
            'Allowed shapes: {"status":"continue","summary":"...","action":{"kind":"capture_screen"}}',
            'or {"status":"delegate","summary":"...","delegate":{"name":"Verifier","instruction":"...","max_iterations":4}}',
            'or {"status":"complete","summary":"..."}.',
            "Do not add markdown fences or commentary.",
            "",
            content.strip(),
        ]
    )


def _parse_autonomous_plan(
    content: str,
    *,
    default_iterations: int,
) -> _AutonomousPlan:
    payload = _extract_json_object(content)
    if payload is None:
        inferred = _infer_autonomous_plan(content, default_iterations=default_iterations)
        if inferred is not None:
            return inferred
        raise ValueError("Autonomous planner did not return valid JSON.")

    status = str(payload.get("status") or "complete").strip().lower()
    summary = str(payload.get("summary") or "").strip()

    if status == "continue":
        action = payload.get("action")
        if not isinstance(action, dict):
            raise ValueError("Autonomous planner returned continue without an action.")
        return _AutonomousPlan(
            status="continue",
            summary=summary or "Planner selected the next action.",
            step=_workflow_step_from_action(action),
        )

    if status == "delegate":
        delegate = payload.get("delegate")
        if not isinstance(delegate, dict):
            raise ValueError("Autonomous planner returned delegate without a delegate payload.")
        name = str(delegate.get("name") or "Subagent").strip()
        instruction = str(delegate.get("instruction") or "").strip()
        if not instruction:
            raise ValueError("Delegate actions require delegate.instruction.")
        max_iterations = int(delegate.get("max_iterations") or default_iterations)
        model_assignment = _delegate_model_assignment(delegate)
        return _AutonomousPlan(
            status="delegate",
            summary=summary or f"Delegated work to {name}.",
            delegate_name=name,
            delegate_instruction=instruction,
            delegate_max_iterations=max(1, min(max_iterations, 24)),
            delegate_model_assignment=model_assignment,
        )

    return _AutonomousPlan(
        status="complete",
        summary=summary or "Agent marked the task complete.",
    )


def _workflow_step_from_action(action: dict) -> WorkflowStepDefinition:
    kind_value = str(action.get("kind") or "").strip()
    if not kind_value:
        raise ValueError("Planner action is missing kind.")
    kind = WorkflowStepType(kind_value)
    if kind in {WorkflowStepType.DELEGATE_AGENT, WorkflowStepType.COMPLETE}:
        raise ValueError("Planner continue actions cannot use delegate_agent or complete.")
    payload: dict[str, object] = {"kind": kind}
    text = action.get("text")
    if isinstance(text, str) and text.strip():
        payload["text"] = text.strip()
    image_path = action.get("image_path")
    if isinstance(image_path, str) and image_path.strip():
        payload["image_path"] = image_path.strip()
    runtime = action.get("runtime")
    if isinstance(runtime, str) and runtime.strip():
        payload["runtime"] = ScriptRuntime(runtime.strip())
    return WorkflowStepDefinition.model_validate(payload)


def _delegate_model_assignment(delegate: dict) -> AgentModelAssignment | None:
    nested_assignment = delegate.get("model_assignment")
    if isinstance(nested_assignment, dict):
        return AgentModelAssignment.model_validate(nested_assignment)

    flat_assignment = {
        "provider": delegate.get("provider"),
        "model": delegate.get("model"),
        "base_url": delegate.get("base_url"),
        "assignment_reason": delegate.get("assignment_reason"),
    }
    if any(flat_assignment.get(key) for key in ("provider", "model", "base_url")):
        return AgentModelAssignment.model_validate(flat_assignment)
    return None


def _build_analysis_request(
    prompt: str,
    *,
    operator_guidance: list[str] | None = None,
) -> str:
    lines = [prompt]
    if operator_guidance:
        lines.extend(
            [
                "",
                "Operator guidance:",
                *[f"- {item}" for item in operator_guidance[-5:] if item.strip()],
            ]
        )
    lines.extend(
        [
            "",
            "Return JSON only.",
            'Use the shape: {"summary":"...","suggested_steps":[{"kind":"find_text","text":"Settings"}]}',
            (
                "Allowed kinds: capture_screen, run_ocr, find_text, analyze_image, "
                "move_mouse, left_click, type_text."
            ),
            "Use an empty array when no reliable next action should be suggested.",
            "For move_mouse after a find_text step, you may omit text.",
        ]
    )
    return "\n".join(lines)


def _parse_analysis_response(
    content: str,
    *,
    fallback_image_path: str,
) -> dict[str, object]:
    payload = _extract_json_object(content)
    if payload is None:
        return {"summary": content.strip(), "suggested_steps": []}

    summary = str(payload.get("summary") or "").strip() or content.strip()
    raw_steps = payload.get("suggested_steps")
    if not isinstance(raw_steps, list):
        raw_steps = []
    return {
        "summary": summary,
        "suggested_steps": _normalize_suggested_steps(
            raw_steps,
            fallback_image_path=fallback_image_path,
        ),
    }


def _extract_json_object(content: str) -> dict | None:
    fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    candidates: list[str] = []
    if fenced_match:
        candidates.append(fenced_match.group(1))
    candidates.extend(_balanced_json_candidates(content))

    for candidate in candidates:
        parsed = _parse_json_like_dict(candidate)
        if isinstance(parsed, dict):
            return parsed
    return None


def _balanced_json_candidates(content: str) -> list[str]:
    candidates: list[str] = []
    start_indexes = [index for index, char in enumerate(content) if char == "{"][:24]
    for start in start_indexes:
        depth = 0
        for end in range(start, len(content)):
            char = content[end]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(content[start : end + 1])
                    break
    return candidates


def _parse_json_like_dict(candidate: str) -> dict | None:
    attempts = [
        candidate,
        re.sub(r",(\s*[}\]])", r"\1", candidate),
    ]
    for attempt in attempts:
        try:
            parsed = json.loads(attempt)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(attempt)
            except (ValueError, SyntaxError):
                continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _infer_autonomous_plan(
    content: str,
    *,
    default_iterations: int,
) -> _AutonomousPlan | None:
    normalized = " ".join(content.lower().split())
    summary = content.strip()
    if not summary:
        return None
    if any(
        marker in normalized
        for marker in (
            "task is complete",
            "goal is complete",
            "complete the task",
            "done",
            "finished",
            "no safe next action",
        )
    ):
        return _AutonomousPlan(status="complete", summary=summary)

    kind = _infer_action_kind(normalized)
    if kind is None:
        return None
    action: dict[str, object] = {"kind": kind.value}
    extracted_text = _extract_plan_text_argument(content, kind)
    if extracted_text:
        action["text"] = extracted_text
    if kind in {WorkflowStepType.FIND_TEXT, WorkflowStepType.TYPE_TEXT, WorkflowStepType.EXECUTE_SCRIPT} and "text" not in action:
        return None
    return _AutonomousPlan(
        status="continue",
        summary=summary[:240],
        step=_workflow_step_from_action(action),
        delegate_max_iterations=default_iterations,
    )


def _infer_action_kind(normalized: str) -> WorkflowStepType | None:
    keyword_map: list[tuple[WorkflowStepType, tuple[str, ...]]] = [
        (WorkflowStepType.EXECUTE_SCRIPT, ("execute script", "run script", "python script", "shell command")),
        (WorkflowStepType.RUN_OCR, ("run ocr", "use ocr", "read text", "extract text")),
        (WorkflowStepType.FIND_TEXT, ("find text", "locate text", "look for", "search for")),
        (WorkflowStepType.ANALYZE_IMAGE, ("analyze image", "inspect image", "describe image", "vision model")),
        (WorkflowStepType.CAPTURE_SCREEN, ("capture screen", "take a screenshot", "screenshot", "screen capture")),
        (WorkflowStepType.DETECT_SYSTEM, ("detect system", "identify os", "check the operating system")),
        (WorkflowStepType.MOVE_MOUSE, ("move mouse", "move the cursor", "hover")),
        (WorkflowStepType.LEFT_CLICK, ("left click", "click the button", "click on")),
        (WorkflowStepType.TYPE_TEXT, ("type text", "type ", "enter text", "input text")),
    ]
    for kind, markers in keyword_map:
        if any(marker in normalized for marker in markers):
            return kind
    return None


def _extract_plan_text_argument(content: str, kind: WorkflowStepType) -> str | None:
    quoted = re.search(r"['\"]([^'\"]{1,400})['\"]", content)
    if kind in {WorkflowStepType.FIND_TEXT, WorkflowStepType.TYPE_TEXT} and quoted:
        return quoted.group(1).strip()
    if kind == WorkflowStepType.EXECUTE_SCRIPT:
        fenced = re.search(r"```(?:python|powershell|bash|sh)?\s*(.*?)\s*```", content, re.DOTALL)
        if fenced and fenced.group(1).strip():
            return fenced.group(1).strip()
        if quoted:
            return quoted.group(1).strip()
    return None


def _normalize_suggested_steps(
    raw_steps: list[object],
    *,
    fallback_image_path: str,
) -> list[dict[str, object]]:
    allowed = {
        WorkflowStepType.CAPTURE_SCREEN.value,
        WorkflowStepType.RUN_OCR.value,
        WorkflowStepType.FIND_TEXT.value,
        WorkflowStepType.ANALYZE_IMAGE.value,
        WorkflowStepType.MOVE_MOUSE.value,
        WorkflowStepType.LEFT_CLICK.value,
        WorkflowStepType.TYPE_TEXT.value,
    }
    image_kinds = {
        WorkflowStepType.RUN_OCR.value,
        WorkflowStepType.FIND_TEXT.value,
        WorkflowStepType.ANALYZE_IMAGE.value,
    }
    normalized: list[dict[str, object]] = []

    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "")).strip()
        if kind not in allowed:
            continue
        step: dict[str, object] = {"kind": kind}
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            step["text"] = text.strip()
        if kind in image_kinds:
            image_path = item.get("image_path")
            if isinstance(image_path, str) and image_path.strip():
                step["image_path"] = image_path.strip()
            else:
                step["image_path"] = fallback_image_path
        if kind == WorkflowStepType.TYPE_TEXT.value and "text" not in step:
            continue
        if kind == WorkflowStepType.FIND_TEXT.value and "text" not in step:
            continue
        normalized.append(step)

    return normalized
