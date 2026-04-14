from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from agent_studio.core.models import ChatImageAttachment, ChatRequest, ControlActionPayload
from agent_studio.core.state import SharedState
from agent_studio.services.automation.input_controller import InputController
from agent_studio.services.model_router import ModelRouter
from agent_studio.services.perception.perception_service import PerceptionService


_ALLOWED_ACTIONS = {"move_mouse", "left_click", "type_text"}


class DesktopAgentRuntime:
    """A conversation-independent desktop loop foundation.

    This runtime starts shifting the product away from a workflow-shaped shell and
    toward a direct desktop-agent cycle:

    observe -> summarize -> recommend one next action -> optionally execute.

    It is intentionally standalone in this first patch so it can be wired into
    routes, the main window, or an overlay canvas without depending on the
    workflow service as the primary abstraction.
    """

    def __init__(
        self,
        *,
        state: SharedState,
        perception_service: PerceptionService,
        input_controller: InputController,
        model_router: ModelRouter,
    ) -> None:
        self._state = state
        self._perception_service = perception_service
        self._input_controller = input_controller
        self._model_router = model_router

    async def step(
        self,
        *,
        goal: str,
        image_path: str | None = None,
        auto_execute: bool = False,
    ) -> dict[str, Any]:
        normalized_goal = goal.strip()
        if not normalized_goal:
            raise ValueError("Desktop runtime requires a non-empty goal.")

        capture_path = (image_path or "").strip()
        if not capture_path:
            capture = self._perception_service.capture_screen()
            if not capture.ok or not capture.image_path:
                return {
                    "ok": False,
                    "message": capture.message,
                    "observation": {},
                    "recommended_action": None,
                    "executed": False,
                }
            capture_path = capture.image_path

        self._state.update_ui_state(
            {"latest_capture_path": capture_path}  # accepted by SharedState patch flows
        )

        response = await self._model_router.chat(
            ChatRequest(
                message=_build_runtime_prompt(normalized_goal),
                attachments=[
                    ChatImageAttachment(
                        name=Path(capture_path).name,
                        image_path=capture_path,
                    )
                ],
                system_prompt=(
                    "You are a desktop automation runtime. Inspect the screenshot, "
                    "summarize the interface state, and recommend at most one next "
                    "desktop action. Return JSON only."
                ),
            )
        )
        parsed = _parse_runtime_response(response.content)
        recommended_action = parsed["recommended_action"]
        executed = False
        action_result: dict[str, Any] | None = None

        if auto_execute and isinstance(recommended_action, dict):
            payload = ControlActionPayload(
                action=recommended_action["kind"],
                text=recommended_action.get("text"),
            )
            result = self._input_controller.execute(payload)
            executed = bool(result.allowed and result.executed)
            action_result = result.model_dump(mode="json")

        return {
            "ok": True,
            "message": parsed["summary"] or "Desktop observation complete.",
            "observation": {
                "image_path": capture_path,
                "summary": parsed["summary"],
                "provider": response.provider.value,
                "model": response.model,
                "vision_used": response.vision_used,
                "attachment_count": response.attachment_count,
                "raw_model_output": response.content,
            },
            "recommended_action": recommended_action,
            "executed": executed,
            "action_result": action_result,
        }


def _build_runtime_prompt(goal: str) -> str:
    return "\n".join(
        [
            f"Goal: {goal}",
            "",
            "Return JSON only.",
            (
                'Use the shape: '
                '{"summary":"...","recommended_action":null} '
                'or {"summary":"...","recommended_action":{"kind":"move_mouse","text":"640,360","why":"...","confidence":0.82}}'
            ),
            "Allowed action kinds in this first runtime foundation: move_mouse, left_click, type_text.",
            "Use text for coordinates or typed content when needed.",
            "If no safe and reliable next action is clear, set recommended_action to null.",
        ]
    )


def _parse_runtime_response(content: str) -> dict[str, Any]:
    payload = _extract_json_object(content)
    if payload is None:
        return {
            "summary": content.strip(),
            "recommended_action": None,
        }

    summary = str(payload.get("summary") or content.strip() or "").strip()
    recommended_action = _parse_recommended_action(payload.get("recommended_action"))
    return {
        "summary": summary,
        "recommended_action": recommended_action,
    }


def _parse_recommended_action(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    kind = str(value.get("kind") or "").strip()
    if kind not in _ALLOWED_ACTIONS:
        return None
    text = value.get("text")
    why = value.get("why")
    confidence = value.get("confidence", 0.0)
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.0
    return {
        "kind": kind,
        "text": str(text).strip() if text is not None else None,
        "why": str(why).strip() if why is not None else None,
        "confidence": max(0.0, min(confidence_value, 1.0)),
    }


def _extract_json_object(content: str) -> dict[str, Any] | None:
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


def _parse_json_like_dict(candidate: str) -> dict[str, Any] | None:
    attempts = [candidate, re.sub(r",(\s*[}\]])", r"\1", candidate)]
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
