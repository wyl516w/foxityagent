from agent_studio.core.config import AppConfig
from agent_studio.core.models import AutomationSettingsPayload, ControlMode
from agent_studio.core.state import SharedState
from agent_studio.services.automation.permission_manager import PermissionManager


def test_deny_mode_blocks_actions() -> None:
    state = SharedState(config=AppConfig())
    state.update_automation_settings(AutomationSettingsPayload(control_mode=ControlMode.DENY))
    manager = PermissionManager(state=state)

    decision = manager.evaluate("demo:left_click")

    assert decision.allowed is False
    assert decision.mode == ControlMode.DENY


def test_allow_session_permits_actions() -> None:
    state = SharedState(config=AppConfig())
    state.update_automation_settings(
        AutomationSettingsPayload(control_mode=ControlMode.ALLOW_SESSION)
    )
    manager = PermissionManager(state=state)

    decision = manager.evaluate("demo:type_text")

    assert decision.allowed is True
    assert decision.requires_confirmation is False
