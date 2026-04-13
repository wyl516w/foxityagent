from agent_studio.core.config import AppConfig
from agent_studio.core.models import AutomationSettingsPayload, ControlActionPayload, ControlMode
from agent_studio.core.state import SharedState
from agent_studio.services.automation import controller_factory
from agent_studio.services.automation.noop_controller import NoopInputController
from agent_studio.services.automation.permission_manager import PermissionManager
from agent_studio.services.automation.windows_controller import WindowsInputController


class _FakeUser32:
    def __init__(self) -> None:
        self.cursor_positions: list[tuple[int, int]] = []
        self.send_input_counts: list[int] = []

    def SetCursorPos(self, x: int, y: int) -> int:
        self.cursor_positions.append((x, y))
        return 1

    def SendInput(self, count, inputs, size) -> int:
        self.send_input_counts.append(count)
        return count


def test_windows_controller_moves_mouse_when_allowed() -> None:
    state = SharedState(config=AppConfig())
    state.update_automation_settings(
        AutomationSettingsPayload(control_mode=ControlMode.ALLOW_SESSION)
    )
    controller = WindowsInputController(
        state=state,
        permission_manager=PermissionManager(state=state),
        user32=_FakeUser32(),
    )

    result = controller.execute(
        ControlActionPayload(action="move_mouse", text="640,360")
    )

    assert result.allowed is True
    assert result.executed is True
    assert "640, 360" in result.message


def test_windows_controller_blocks_invalid_move_payload() -> None:
    state = SharedState(config=AppConfig())
    state.update_automation_settings(
        AutomationSettingsPayload(control_mode=ControlMode.ALLOW_SESSION)
    )
    controller = WindowsInputController(
        state=state,
        permission_manager=PermissionManager(state=state),
        user32=_FakeUser32(),
    )

    result = controller.execute(
        ControlActionPayload(action="move_mouse", text="not-a-coordinate")
    )

    assert result.allowed is True
    assert result.executed is False
    assert "coordinates" in result.message.lower()


def test_windows_controller_types_text_with_stubbed_sender() -> None:
    state = SharedState(config=AppConfig())
    state.update_automation_settings(
        AutomationSettingsPayload(control_mode=ControlMode.ALLOW_SESSION)
    )
    controller = WindowsInputController(
        state=state,
        permission_manager=PermissionManager(state=state),
        user32=_FakeUser32(),
    )
    captured: list[str] = []
    controller._send_text = lambda text: captured.append(text)  # type: ignore[method-assign]

    result = controller.execute(ControlActionPayload(action="type_text", text="hello"))

    assert result.executed is True
    assert captured == ["hello"]


def test_controller_factory_falls_back_to_noop_outside_windows(monkeypatch) -> None:
    state = SharedState(config=AppConfig())
    permission_manager = PermissionManager(state=state)
    monkeypatch.setattr(controller_factory.platform, "system", lambda: "Linux")

    controller = controller_factory.build_input_controller(
        state=state,
        permission_manager=permission_manager,
    )

    assert isinstance(controller, NoopInputController)


def test_controller_factory_uses_windows_controller_on_windows(monkeypatch) -> None:
    state = SharedState(config=AppConfig())
    permission_manager = PermissionManager(state=state)

    class _StubWindowsController:
        def __init__(self, state, permission_manager) -> None:
            self.state = state
            self.permission_manager = permission_manager

        @property
        def controller_name(self) -> str:
            return "windows_real"

        def execute(self, payload):
            raise NotImplementedError

    monkeypatch.setattr(controller_factory.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        controller_factory,
        "WindowsInputController",
        _StubWindowsController,
    )

    controller = controller_factory.build_input_controller(
        state=state,
        permission_manager=permission_manager,
    )

    assert controller.controller_name == "windows_real"
