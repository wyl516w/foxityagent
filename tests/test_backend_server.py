import pytest

from agent_studio.core.config import AppConfig
from agent_studio.core.state import SharedState
from agent_studio.services.backend_server import BackendServer


def test_backend_server_start_fails_fast_when_health_never_recovers(monkeypatch) -> None:
    config = AppConfig(backend_port=8877)
    state = SharedState(config=config)
    server = BackendServer(config=config, state=state)

    monkeypatch.setattr(server._server, "run", lambda: None)
    monkeypatch.setattr(server, "wait_until_ready", lambda timeout_seconds: False)

    with pytest.raises(RuntimeError, match="failed to become ready"):
        server.start()

    assert any(
        "failed to become ready" in event.lower() for event in state.get_recent_events()
    )


def test_backend_server_wires_desktop_runtime_route() -> None:
    config = AppConfig(backend_port=8878)
    state = SharedState(config=config)
    server = BackendServer(config=config, state=state)

    assert server.desktop_runtime is not None
    assert any(
        getattr(route, "path", None) == "/api/agent/runtime/step"
        for route in server.app.routes
    )
