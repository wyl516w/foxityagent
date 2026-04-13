from __future__ import annotations

from agent_studio.core.models import ControlActionPayload, ControlActionResult
from agent_studio.core.state import SharedState
from agent_studio.services.automation.input_controller import InputController
from agent_studio.services.automation.permission_manager import PermissionManager


class NoopInputController(InputController):
    def __init__(self, state: SharedState, permission_manager: PermissionManager) -> None:
        self._state = state
        self._permission_manager = permission_manager

    @property
    def controller_name(self) -> str:
        return "noop"

    def execute(self, payload: ControlActionPayload) -> ControlActionResult:
        decision = self._permission_manager.evaluate(reason=payload.action.value)
        if not decision.allowed:
            return ControlActionResult(
                allowed=False,
                executed=False,
                message=decision.message,
                event=f"blocked:{payload.action.value}",
            )

        detail = payload.text.strip() if payload.text else "no payload"
        event = f"simulated:{payload.action.value}:{detail}"
        self._state.append_event(
            f"Noop controller accepted {payload.action.value} with payload '{detail}'."
        )
        return ControlActionResult(
            allowed=True,
            executed=False,
            message=(
                "Permission granted, but this platform is using the noop controller. "
                "No real desktop input was emitted."
            ),
            event=event,
        )
