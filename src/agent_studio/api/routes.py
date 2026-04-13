from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agent_studio.core.config import AppConfig
from agent_studio.core.models import (
    AgentModelAssignment,
    AppSettingsUpdateRequest,
    AutomationSettingsPayload,
    ChatImageAttachment,
    ChatRequest,
    ChatResponse,
    ConversationHistoryResponse,
    ConversationListResponse,
    ControlActionPayload,
    ControlActionResult,
    CreateConversationRequest,
    DeleteConversationResponse,
    CreateTaskAgentRequest,
    CreateWorkflowTaskRequest,
    ElementLookupRequest,
    ElementLookupResponse,
    HealthResponse,
    OcrRequest,
    OcrResponse,
    ProviderCapabilitiesResponse,
    ProviderHealthResponse,
    ProviderHealthSweepResponse,
    ProviderSettingsPayload,
    ProviderType,
    ScriptExecutionPrepareRequest,
    ScriptExecutionPreviewResponse,
    ScriptExecutionResponse,
    ScriptExecutionRunRequest,
    ScreenshotResponse,
    SettingsSnapshot,
    SystemInfoResponse,
    UiStatePayload,
    WorkflowApprovalDecisionRequest,
    WorkflowAgentTreeResponse,
    WorkflowRunResponse,
    WorkflowTaskDetail,
    WorkflowTaskDetailListResponse,
    WorkflowTaskListResponse,
)
from agent_studio.core.state import SharedState
from agent_studio.services.automation.input_controller import InputController
from agent_studio.services.conversation_service import ConversationService
from agent_studio.services.automation.permission_manager import PermissionManager
from agent_studio.services.model_router import ModelRouter
from agent_studio.services.perception.perception_service import PerceptionService
from agent_studio.services.system.system_service import SystemService
from agent_studio.services.workflows.workflow_service import WorkflowService


def build_router(
    config: AppConfig,
    state: SharedState,
    model_router: ModelRouter,
    permission_manager: PermissionManager,
    input_controller: InputController,
    conversation_service: ConversationService | None,
    perception_service: PerceptionService | None,
    workflow_service: WorkflowService | None = None,
    system_service: SystemService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        provider = state.get_provider_settings()
        automation = state.get_automation_settings()
        return HealthResponse(
            app_name=config.app_name,
            backend_url=config.backend_url,
            provider=provider.provider,
            control_mode=automation.control_mode,
            input_controller=input_controller.controller_name,
            event_count=len(state.get_recent_events()),
        )

    @router.get("/settings", response_model=SettingsSnapshot)
    async def settings() -> SettingsSnapshot:
        return SettingsSnapshot(
            provider=state.get_provider_settings(),
            automation=state.get_automation_settings(),
            ui=state.get_ui_state(),
            recent_events=state.get_recent_events(),
        )

    @router.post("/settings/provider/update", response_model=SettingsSnapshot)
    async def set_provider_settings(payload: ProviderSettingsPayload) -> SettingsSnapshot:
        provider = state.update_provider_settings(payload)
        return SettingsSnapshot(
            provider=provider,
            automation=state.get_automation_settings(),
            ui=state.get_ui_state(),
            recent_events=state.get_recent_events(),
        )

    @router.post("/settings/automation", response_model=SettingsSnapshot)
    async def set_automation_settings(
        payload: AutomationSettingsPayload,
    ) -> SettingsSnapshot:
        automation = state.update_automation_settings(payload)
        return SettingsSnapshot(
            provider=state.get_provider_settings(),
            automation=automation,
            ui=state.get_ui_state(),
            recent_events=state.get_recent_events(),
        )

    @router.post("/settings/ui", response_model=SettingsSnapshot)
    async def set_ui_settings(payload: UiStatePayload) -> SettingsSnapshot:
        ui_state = state.update_ui_state(payload)
        return SettingsSnapshot(
            provider=state.get_provider_settings(),
            automation=state.get_automation_settings(),
            ui=ui_state,
            recent_events=state.get_recent_events(),
        )

    @router.post("/settings/apply", response_model=SettingsSnapshot)
    async def apply_settings(payload: AppSettingsUpdateRequest) -> SettingsSnapshot:
        if payload.provider is not None:
            state.update_provider_settings(payload.provider)
        if payload.automation is not None:
            state.update_automation_settings(payload.automation)
        if payload.ui is not None:
            state.update_ui_state(payload.ui)
        return SettingsSnapshot(
            provider=state.get_provider_settings(),
            automation=state.get_automation_settings(),
            ui=state.get_ui_state(),
            recent_events=state.get_recent_events(),
        )

    @router.post("/provider/health", response_model=ProviderHealthResponse)
    async def check_provider_health(
        payload: ProviderSettingsPayload,
    ) -> ProviderHealthResponse:
        try:
            result = await model_router.check_provider(payload)
            state.append_event(
                f"Provider health check completed for {payload.provider.value}: {'ok' if result.ok else 'failed'}."
            )
            return result
        except Exception as exc:  # pragma: no cover - surfaced to UI
            state.append_event(f"Provider health check failed: {exc}")
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/provider/health/all", response_model=ProviderHealthSweepResponse)
    async def check_all_provider_health(
        payload: ProviderSettingsPayload,
    ) -> ProviderHealthSweepResponse:
        try:
            result = await model_router.check_all_providers(payload)
            state.append_event(
                "Provider sweep completed: "
                f"{result.ok_count}/{len(result.results)} routes ready."
            )
            return result
        except Exception as exc:  # pragma: no cover - surfaced to UI
            state.append_event(f"Provider sweep failed: {exc}")
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/provider/capabilities", response_model=ProviderCapabilitiesResponse)
    async def describe_provider_capabilities(
        payload: ProviderSettingsPayload,
    ) -> ProviderCapabilitiesResponse:
        return model_router.describe_capabilities(payload)

    @router.get("/system/info", response_model=SystemInfoResponse)
    async def get_system_info() -> SystemInfoResponse:
        _require_system_service(system_service)
        return system_service.get_system_info()

    @router.post(
        "/system/script/prepare",
        response_model=ScriptExecutionPreviewResponse,
    )
    async def prepare_script_execution(
        payload: ScriptExecutionPrepareRequest,
    ) -> ScriptExecutionPreviewResponse:
        _require_system_service(system_service)
        preview = await system_service.prepare_script_execution(payload)
        state.append_event(
            f"Prepared script execution request {preview.confirmation_id} ({preview.runtime.value})."
        )
        return preview

    @router.post(
        "/system/script/execute",
        response_model=ScriptExecutionResponse,
    )
    async def execute_prepared_script(
        payload: ScriptExecutionRunRequest,
    ) -> ScriptExecutionResponse:
        _require_system_service(system_service)
        try:
            result = system_service.execute_prepared_script(payload)
        except ValueError as exc:
            state.append_event(f"Script execution blocked: {exc}")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        state.append_event(
            f"Script execution {'completed' if result.ok else 'finished with errors'}: "
            f"{result.confirmation_id}."
        )
        return result

    @router.post("/perception/capture", response_model=ScreenshotResponse)
    async def capture_screen() -> ScreenshotResponse:
        _require_perception_service(perception_service)
        result = perception_service.capture_screen()
        if result.ok and result.image_path:
            state.update_ui_state(UiStatePayload(latest_capture_path=result.image_path))
            state.append_event(f"Screenshot captured: {result.image_path}")
        else:
            state.append_event(f"Screenshot capture failed: {result.message}")
        return result

    @router.post("/perception/ocr", response_model=OcrResponse)
    async def run_ocr(payload: OcrRequest) -> OcrResponse:
        _require_perception_service(perception_service)
        image_path = _resolve_image_path(state=state, image_path=payload.image_path)
        result = perception_service.run_ocr(image_path)
        state.append_event(
            f"OCR {'completed' if result.ok else 'failed'} for {image_path}."
        )
        return result

    @router.post("/perception/find", response_model=ElementLookupResponse)
    async def find_text(payload: ElementLookupRequest) -> ElementLookupResponse:
        _require_perception_service(perception_service)
        image_path = _resolve_image_path(state=state, image_path=payload.image_path)
        result = perception_service.find_text(
            image_path=image_path,
            query=payload.query,
            case_sensitive=payload.case_sensitive,
        )
        state.append_event(
            f"Element lookup for '{payload.query}' returned {len(result.matches)} matches."
        )
        return result

    @router.get("/conversations", response_model=ConversationListResponse)
    async def list_conversations() -> ConversationListResponse:
        _require_conversation_service(conversation_service)
        return conversation_service.list_conversations()

    @router.post("/conversations", response_model=ConversationHistoryResponse)
    async def create_conversation(
        payload: CreateConversationRequest,
    ) -> ConversationHistoryResponse:
        _require_conversation_service(conversation_service)
        summary = conversation_service.create_conversation(payload.title)
        state.update_ui_state(UiStatePayload(current_conversation_id=summary.conversation_id))
        return conversation_service.get_history(summary.conversation_id)

    @router.delete(
        "/conversations/{conversation_id}",
        response_model=DeleteConversationResponse,
    )
    async def delete_conversation(conversation_id: str) -> DeleteConversationResponse:
        _require_conversation_service(conversation_service)
        try:
            response = conversation_service.delete_conversation(conversation_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        ui_state = state.get_ui_state()
        if ui_state.current_conversation_id == conversation_id:
            state.update_ui_state(UiStatePayload(current_conversation_id=None))
        state.append_event(f"Conversation deleted: {conversation_id}")
        return response

    @router.get(
        "/conversations/{conversation_id}",
        response_model=ConversationHistoryResponse,
    )
    async def get_conversation(conversation_id: str) -> ConversationHistoryResponse:
        _require_conversation_service(conversation_service)
        try:
            return conversation_service.get_history(conversation_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/conversations/{conversation_id}/tasks",
        response_model=WorkflowTaskListResponse,
    )
    async def list_conversation_tasks(conversation_id: str) -> WorkflowTaskListResponse:
        _require_workflow_service(workflow_service)
        return workflow_service.list_tasks(conversation_id=conversation_id)

    @router.get(
        "/conversations/{conversation_id}/tasks/details",
        response_model=WorkflowTaskDetailListResponse,
    )
    async def list_conversation_task_details(
        conversation_id: str,
    ) -> WorkflowTaskDetailListResponse:
        _require_workflow_service(workflow_service)
        return WorkflowTaskDetailListResponse(
            tasks=await workflow_service.list_task_details_for_conversation(conversation_id)
        )

    @router.post("/chat", response_model=ChatResponse)
    async def chat(payload: ChatRequest) -> ChatResponse:
        try:
            conversation_id = payload.conversation_id
            request_attachments = payload.attachments
            seed_text = (
                payload.message
                or _first_attachment_label(request_attachments)
                or "Review the current desktop state and complete the requested goal."
            )
            if conversation_service is not None:
                summary = conversation_service.ensure_conversation(
                    conversation_id=payload.conversation_id,
                    seed_message=seed_text,
                )
                conversation_id = summary.conversation_id
                state.update_ui_state(
                    UiStatePayload(current_conversation_id=conversation_id)
                )
                try:
                    request_attachments = conversation_service.materialize_attachments_for_conversation(
                        conversation_id=conversation_id,
                        attachments=payload.attachments,
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
            latest_image_path = _latest_attachment_path(request_attachments)
            if latest_image_path:
                state.update_ui_state(UiStatePayload(latest_capture_path=latest_image_path))
            request_payload = payload.model_copy(update={"attachments": request_attachments})
            user_message = None
            if conversation_service is not None and conversation_id is not None:
                user_message = conversation_service.append_message(
                    conversation_id=conversation_id,
                    role="user",
                    content=payload.message,
                    attachments=_attachments_for_storage(request_attachments),
                )
            task: WorkflowTaskDetail | None = None
            primary_failure_reason: str | None = None
            provider_settings = state.get_provider_settings()

            if workflow_service is not None and conversation_id is not None:
                try:
                    task_request = CreateWorkflowTaskRequest(
                        title=None,
                        conversation_id=conversation_id,
                        instruction=seed_text,
                        source_message_id=user_message.message_id if user_message is not None else None,
                        source_message_preview=seed_text,
                        model_assignment=_assignment_from_provider_settings(provider_settings),
                        autonomous=True,
                        max_iterations=8,
                        preferred_language="system",
                        steps=[],
                    )
                    task = workflow_service.create_task(task_request)
                    if conversation_service is not None and user_message is not None:
                        conversation_service.link_message_to_task(
                            message_id=user_message.message_id,
                            task_id=task.task_id,
                        )
                except Exception as exc:
                    primary_failure_reason = f"autonomous task creation failed: {exc}"
                else:
                    try:
                        task = (await workflow_service.run_task(task.task_id)).task
                    except Exception as exc:
                        primary_failure_reason = f"autonomous task execution failed: {exc}"
                        refreshed_task = await _try_reload_task(
                            workflow_service=workflow_service,
                            task_id=task.task_id,
                        )
                        if refreshed_task is not None:
                            task = refreshed_task
                    else:
                        task_response = _build_chat_task_response(
                            payload=request_payload,
                            conversation_id=conversation_id,
                            task=task,
                            config=config,
                        )
                        should_fallback, fallback_reason = _should_fallback_to_direct_chat(
                            task=task,
                            content=task_response.content,
                        )
                        if not should_fallback:
                            if conversation_service is not None:
                                conversation_service.append_message(
                                    conversation_id=conversation_id,
                                    role="assistant",
                                    content=task_response.content,
                                    linked_task_id=task.task_id,
                                )
                            state.append_event(
                                "Chat created autonomous task "
                                f"{task.task_id} for conversation {conversation_id} using "
                                f"{provider_settings.provider.value}/{provider_settings.model}."
                            )
                            return task_response
                        primary_failure_reason = (
                            fallback_reason
                            or "autonomous task returned a low-signal response."
                        )

            if primary_failure_reason is not None:
                state.append_event(
                    f"Chat autonomous route fallback reason: {primary_failure_reason}"
                )
                state.append_event("Chat switched to direct model fallback response.")

            try:
                response = await model_router.chat(
                    request_payload.model_copy(update={"conversation_id": conversation_id})
                )
            except Exception as exc:
                if primary_failure_reason is not None:
                    detail = (
                        "Chat primary failure: "
                        f"{primary_failure_reason}; fallback failure: {exc}"
                    )
                else:
                    detail = f"Direct chat failed: {exc}"
                state.append_event(f"Chat request failed: {detail}")
                raise HTTPException(status_code=502, detail=detail) from exc

            response = _merge_chat_response_with_task(
                response=response,
                conversation_id=conversation_id,
                task=task,
            )

            if conversation_service is not None and conversation_id is not None:
                conversation_service.append_message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=response.content,
                    linked_task_id=task.task_id if task is not None else None,
                )
            if response.fallback_used:
                attempted = " -> ".join(
                    provider.value for provider in response.attempted_providers
                )
                state.append_event(
                    f"Chat response used fallback route {attempted}: {response.fallback_reason}"
                )
            else:
                state.append_event(
                    f"Chat response generated by {response.provider.value}"
                    f" with {response.attachment_count} image attachment(s)."
                )
            return response
        except HTTPException:
            raise
        except Exception as exc:  # pragma: no cover - surfaced to UI
            state.append_event(f"Chat request failed: {exc}")
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get("/tasks", response_model=WorkflowTaskListResponse)
    async def list_tasks() -> WorkflowTaskListResponse:
        _require_workflow_service(workflow_service)
        return workflow_service.list_tasks()

    @router.post("/tasks", response_model=WorkflowTaskDetail)
    async def create_task(payload: CreateWorkflowTaskRequest) -> WorkflowTaskDetail:
        _require_workflow_service(workflow_service)
        try:
            return workflow_service.create_task(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/tasks/{task_id}", response_model=WorkflowTaskDetail)
    async def get_task(task_id: str) -> WorkflowTaskDetail:
        _require_workflow_service(workflow_service)
        try:
            return await workflow_service.get_task(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/tasks/{task_id}/agents", response_model=WorkflowTaskDetail)
    async def add_agent(
        task_id: str,
        payload: CreateTaskAgentRequest,
    ) -> WorkflowTaskDetail:
        _require_workflow_service(workflow_service)
        try:
            return workflow_service.add_agent(task_id=task_id, request=payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/tasks/{task_id}/agents/tree", response_model=WorkflowAgentTreeResponse)
    async def get_agent_tree(task_id: str) -> WorkflowAgentTreeResponse:
        _require_workflow_service(workflow_service)
        try:
            return workflow_service.get_agent_tree(task_id=task_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/tasks/{task_id}/run", response_model=WorkflowRunResponse)
    async def run_task(task_id: str) -> WorkflowRunResponse:
        _require_workflow_service(workflow_service)
        try:
            return await workflow_service.run_task(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/tasks/{task_id}/approve", response_model=WorkflowRunResponse)
    async def approve_task_pending_step(
        task_id: str,
        payload: WorkflowApprovalDecisionRequest,
    ) -> WorkflowRunResponse:
        _require_workflow_service(workflow_service)
        try:
            return await workflow_service.approve_pending_step(
                task_id,
                decision=payload.decision,
                extra_prompt=payload.extra_prompt,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/automation/demo", response_model=ControlActionResult)
    async def automation_demo(payload: ControlActionPayload) -> ControlActionResult:
        decision = permission_manager.evaluate(reason=f"demo:{payload.action.value}")
        if not decision.allowed:
            state.append_event(
                f"Control request blocked by mode {decision.mode.value}: {payload.action.value}."
            )
            return ControlActionResult(
                allowed=False,
                executed=False,
                message=decision.message,
                event=f"blocked:{payload.action.value}",
            )

        return input_controller.execute(payload)

    return router


def _require_conversation_service(
    conversation_service: ConversationService | None,
) -> None:
    if conversation_service is None:
        raise HTTPException(
            status_code=503,
            detail="Conversation persistence is unavailable in this runtime.",
        )


def _require_perception_service(perception_service: PerceptionService | None) -> None:
    if perception_service is None:
        raise HTTPException(
            status_code=503,
            detail="Perception services are unavailable in this runtime.",
        )


def _require_workflow_service(workflow_service: WorkflowService | None) -> None:
    if workflow_service is None:
        raise HTTPException(
            status_code=503,
            detail="Workflow services are unavailable in this runtime.",
        )


def _require_system_service(system_service: SystemService | None) -> None:
    if system_service is None:
        raise HTTPException(
            status_code=503,
            detail="System services are unavailable in this runtime.",
        )


def _resolve_image_path(state: SharedState, image_path: str | None) -> str:
    if image_path:
        return image_path
    latest_capture_path = state.get_ui_state().latest_capture_path
    if latest_capture_path:
        return latest_capture_path
    raise HTTPException(
        status_code=400,
        detail="No image_path was provided and no latest capture is available yet.",
    )


def _attachments_for_storage(
    attachments: list[ChatImageAttachment],
) -> list[ChatImageAttachment]:
    stored: list[ChatImageAttachment] = []
    for attachment in attachments:
        normalized_name = attachment.name
        if not normalized_name and attachment.image_path:
            normalized_name = attachment.image_path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        if not normalized_name:
            normalized_name = "inline-image"
        stored.append(
            attachment.model_copy(
                update={
                    "name": normalized_name,
                    "image_base64": None,
                }
            )
        )
    return stored


def _first_attachment_label(attachments: list[ChatImageAttachment]) -> str | None:
    for attachment in attachments:
        if attachment.name:
            return attachment.name
        if attachment.image_path:
            return attachment.image_path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    return None


def _latest_attachment_path(attachments: list[ChatImageAttachment]) -> str | None:
    for attachment in reversed(attachments):
        if attachment.image_path:
            return attachment.image_path
    return None


def _build_chat_task_response(
    *,
    payload: ChatRequest,
    conversation_id: str,
    task: WorkflowTaskDetail,
    config: AppConfig,
) -> ChatResponse:
    root_agent = next(
        (agent for agent in task.agents if agent.parent_agent_id is None),
        task.agents[0] if task.agents else None,
    )
    assignment = root_agent.model_assignment if root_agent is not None else None
    provider = (
        assignment.provider
        if assignment is not None and assignment.provider is not None
        else ProviderType.OLLAMA
    )
    model = (
        assignment.model
        if assignment is not None and assignment.model
        else config.default_local_model
    )

    status_value = task.status.value
    content = (task.last_message or "").strip()
    generic_messages = {
        "Task is running.",
        "Task created and ready to run.",
    }
    if status_value in {"completed", "failed", "waiting_approval"} and content in generic_messages:
        content = ""
    if task.pending_approval:
        summary = str(task.pending_approval.get("summary") or content).strip()
        warnings = task.pending_approval.get("warnings") or []
        lines = [summary or "The task is waiting for approval."]
        if warnings:
            lines.append("")
            lines.extend(f"- {warning}" for warning in warnings[:4])
        content = "\n".join(lines)
    elif not content:
        last_result = next(
            (result for result in reversed(task.results) if result.message.strip()),
            None,
        )
        if last_result is not None:
            content = last_result.message.strip()
    if not content:
        fallback_messages = {
            "completed": f"Task '{task.title}' completed.",
            "waiting_approval": f"Task '{task.title}' is waiting for approval.",
            "failed": f"Task '{task.title}' failed.",
        }
        content = fallback_messages.get(status_value, f"Task '{task.title}' is running.")

    return ChatResponse(
        provider=provider,
        model=model,
        content=content,
        conversation_id=conversation_id,
        task_id=task.task_id,
        task_status=status_value,
        task_title=task.title,
        used_mock=provider == ProviderType.MOCK,
        vision_used=bool(payload.attachments),
        attachment_count=len(payload.attachments),
        latency_ms=0,
    )


def _assignment_from_provider_settings(
    provider_settings: ProviderSettingsPayload,
) -> AgentModelAssignment:
    return AgentModelAssignment(
        provider=provider_settings.provider,
        model=provider_settings.model,
        base_url=provider_settings.base_url,
        assignment_reason=(
            "Chat tasks inherit the current provider settings so the conversation route "
            "stays aligned with the active model configuration."
        ),
    )


def _should_fallback_to_direct_chat(
    *,
    task: WorkflowTaskDetail,
    content: str,
) -> tuple[bool, str | None]:
    status_value = task.status.value
    if status_value == "failed":
        return True, "autonomous task ended with status failed"

    normalized_content = content.strip()
    if not normalized_content:
        return True, "autonomous task returned empty content"

    if _is_task_status_placeholder(normalized_content):
        return True, "autonomous task returned a placeholder status message"

    lowered_content = normalized_content.lower()
    low_signal_markers = (
        "autonomous planning failed",
        "planner did not return valid json",
        "autonomous planning reached the maximum number of iterations",
        "task is running.",
        "task created and ready to run.",
    )
    for marker in low_signal_markers:
        if marker in lowered_content:
            return True, f"autonomous task returned low-signal content: {marker}"
    return False, None


def _is_task_status_placeholder(content: str) -> bool:
    lowered = content.strip().lower()
    return lowered.startswith("task '") and (
        lowered.endswith("' completed.")
        or lowered.endswith("' failed.")
        or lowered.endswith("' is waiting for approval.")
        or lowered.endswith("' is running.")
    )


def _merge_chat_response_with_task(
    *,
    response: ChatResponse,
    conversation_id: str | None,
    task: WorkflowTaskDetail | None,
) -> ChatResponse:
    updates: dict[str, str | None] = {"conversation_id": conversation_id}
    if task is not None:
        updates.update(
            {
                "task_id": task.task_id,
                "task_status": task.status.value,
                "task_title": task.title,
            }
        )
    return response.model_copy(update=updates)


async def _try_reload_task(
    *,
    workflow_service: WorkflowService,
    task_id: str,
) -> WorkflowTaskDetail | None:
    try:
        return await workflow_service.get_task(task_id)
    except Exception:
        return None
