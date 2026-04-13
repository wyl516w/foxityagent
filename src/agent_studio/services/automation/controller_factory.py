from __future__ import annotations

import platform

from agent_studio.core.state import SharedState
from agent_studio.services.automation.input_controller import InputController
from agent_studio.services.automation.noop_controller import NoopInputController
from agent_studio.services.automation.permission_manager import PermissionManager
from agent_studio.services.automation.windows_controller import WindowsInputController


def build_input_controller(
    state: SharedState,
    permission_manager: PermissionManager,
) -> InputController:
    if platform.system() == "Windows":
        return WindowsInputController(
            state=state,
            permission_manager=permission_manager,
        )

    return NoopInputController(
        state=state,
        permission_manager=permission_manager,
    )

