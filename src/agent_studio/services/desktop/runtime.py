from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from agent_studio.core.models import (
    ChatImageAttachment,
    ChatRequest,
    ControlActionPayload,
    ControlActionType,
    DesktopRuntimeObservation,
    DesktopRuntimeRecommendedAction,
    DesktopRuntimeTargetBBox,
    DesktopRuntimeTargetPoint,
    DesktopRuntimeStepResponse,
    UiStatePayload,
)
from agent_studio.core.state import SharedState
from agent_studio.services.automation.input_controller import InputController
from agent_studio.services.model_router import ModelRouter
from agent_studio.services.perception.perception_service import PerceptionService


_ALLOWED_ACTIONS = {
    ControlActionType.MOVE_MOUSE.value: ControlActionType.MOVE_MOUSE,
    ControlActionType.LEFT_CLICK.value: ControlActionType.LEFT_CLICK,
    ControlActionType.TYPE_TEXT.value: ControlActionType.TYPE_TEXT,
}


class DesktopAgentRuntime:
    """Conversation-independent desktop runtime foundation.

    This runtime starts shifting the product away from a workflow shell and
    toward a direct desktop-agent cycle:

    observe -> interpret -> recommend one next action -> optionally execute.
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
    ) -> DesktopRuntimeStepResponse:
        normalized_goal = goal.strip()
        if not normalized_goal:
            raise ValueError("Desktop runtime requires a non-empty goal.")

        capture_path = (image_path or "").strip()
        if not capture_path:
            capture = self._perception_service.capture_screen()
            if not capture.ok or not capture.image_path:
                return DesktopRuntimeStepResponse(
                    ok=False,
                    message=capture.message,
                    observation=DesktopRuntimeObservation(),
                    executed=False,
                )
            capture_path = capture.image_path

        self._state.update_ui_state(UiStatePayload(latest_capture_path=capture_path))

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
        action_result = None

        if auto_execute and recommended_action is not None:
            action_result = self._input_controller.execute(
                ControlActionPayload(
                    action=recommended_action.kind,
                    text=recommended_action.text,
                )
            )
            executed = bool(action_result.allowed and action_result.executed)

        return DesktopRuntimeStepResponse(
            ok=True,
            message=parsed["summary"] or "Desktop observation complete.",
            observation=DesktopRuntimeObservation(
                image_path=capture_path,
                summary=parsed["summary"],
                provider=response.provider,
                model=response.model,
                vision_used=response.vision_used,
                attachment_count=response.attachment_count,
                raw_model_output=response.content,
            ),
            recommended_action=recommended_action,
            executed=executed,
            action_result=action_result,
        )


def _build_runtime_prompt(goal: str) -> str:
    return "\n".join(
        [
            f"Goal: {goal}",
            "",
            "Observe the screenshot, interpret the desktop state, and recommend one next action.",
            "Return JSON only.",
            (
                'Use the shape '
                '{"summary":"...","recommended_action":null} '
                'or {"summary":"...","recommended_action":{"kind":"move_mouse","text":"640,360","why":"...","confidence":0.82,"'
                'target_point":{"x":640,"y":360},"target_bbox":{"x":560,"y":320,"width":160,"height":80},"annotation_label":"..."}}.'
            ),
            "Allowed action kinds: move_mouse, left_click, type_text.",
            "Use text for coordinates or typed content when needed.",
            "target_point, target_bbox, and annotation_label are optional but preferred when location is clear.",
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


def _parse_recommended_action(value: Any) -> DesktopRuntimeRecommendedAction | None:
    if not isinstance(value, dict):
        return None
    kind = str(value.get("kind") or "").strip()
    action_type = _ALLOWED_ACTIONS.get(kind)
    if action_type is None:
        return None
    text = value.get("text")
    why = value.get("why")
    confidence = value.get("confidence", 0.0)
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.0

    target_point = _parse_target_point(value.get("target_point"))
    if target_point is None:
        target_point = _parse_point_from_text(text)

    target_bbox = _parse_target_bbox(value.get("target_bbox"))
    annotation_label = value.get("annotation_label")

    return DesktopRuntimeRecommendedAction(
        kind=action_type,
        text=str(text).strip() if text is not None else None,
        why=str(why).strip() if why is not None else None,
        confidence=max(0.0, min(confidence_value, 1.0)),
        target_point=target_point,
        target_bbox=target_bbox,
        annotation_label=str(annotation_label).strip() if annotation_label is not None else None,
    )


def _parse_target_point(value: Any) -> DesktopRuntimeTargetPoint | None:
    if not isinstance(value, dict):
        return None
    try:
        x = float(value.get("x"))
        y = float(value.get("y"))
    except (TypeError, ValueError):
        return None
    return DesktopRuntimeTargetPoint(x=x, y=y)


def _parse_target_bbox(value: Any) -> DesktopRuntimeTargetBBox | None:
    if not isinstance(value, dict):
        return None
    try:
        x = float(value.get("x"))
        y = float(value.get("y"))
        width = float(value.get("width"))
        height = float(value.get("height"))
    except (TypeError, ValueError):
        return None
    if width <= 0.0 or height <= 0.0:
        return None
    return DesktopRuntimeTargetBBox(x=x, y=y, width=width, height=height)


def _parse_point_from_text(text: Any) -> DesktopRuntimeTargetPoint | None:
    if not isinstance(text, str):
        return None
    parts = [segment.strip() for segment in text.split(",")]
    if len(parts) != 2:
        return None
    try:
        x = float(parts[0])
        y = float(parts[1])
    except ValueError:
        return None
    return DesktopRuntimeTargetPoint(x=x, y=y)


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
