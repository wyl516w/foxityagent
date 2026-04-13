import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_studio.api.routes import build_router
from agent_studio.core.config import AppConfig
from agent_studio.core.models import (
    ScriptExecutionPrepareRequest,
    ScriptExecutionRunRequest,
)
from agent_studio.core.state import SharedState
from agent_studio.services.automation.noop_controller import NoopInputController
from agent_studio.services.automation.permission_manager import PermissionManager
from agent_studio.services.model_router import ModelRouter
from agent_studio.services.perception.perception_service import PerceptionService
from agent_studio.services.system.system_service import SystemService


def test_system_service_prepares_and_executes_python_script(monkeypatch) -> None:
    config = AppConfig()
    perception_service = PerceptionService(config=config)
    service = SystemService(config=config, perception_service=perception_service)

    preview = service.prepare_script_execution(
        ScriptExecutionPrepareRequest(
            script="import platform\nprint(platform.system())",
            runtime="auto",
            timeout_seconds=5.0,
        )
    )

    assert preview.runtime.value == "python"
    assert preview.requires_confirmation is True

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="Windows\n",
            stderr="",
        ),
    )

    result = service.execute_prepared_script(
        ScriptExecutionRunRequest(
            confirmation_id=preview.confirmation_id,
            confirm=True,
        )
    )

    assert result.ok is True
    assert "Windows" in result.stdout


def test_system_routes_prepare_and_execute_script(monkeypatch) -> None:
    config = AppConfig()
    state = SharedState(config=config)
    permission_manager = PermissionManager(state=state)
    perception_service = PerceptionService(config=config)
    system_service = SystemService(config=config, perception_service=perception_service)
    app = FastAPI()
    app.include_router(
        build_router(
            config=config,
            state=state,
            model_router=ModelRouter(config=config, state=state),
            permission_manager=permission_manager,
            input_controller=NoopInputController(
                state=state,
                permission_manager=permission_manager,
            ),
            conversation_service=None,
            perception_service=perception_service,
            workflow_service=None,
            system_service=system_service,
        )
    )
    client = TestClient(app)

    info_response = client.get("/api/system/info")
    assert info_response.status_code == 200
    assert info_response.json()["python_version"]

    preview_response = client.post(
        "/api/system/script/prepare",
        json={
            "script": "echo hello-agent",
            "runtime": "shell",
            "timeout_seconds": 5.0,
        },
    )
    assert preview_response.status_code == 200
    preview_payload = preview_response.json()
    assert preview_payload["confirmation_id"].startswith("script-")

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="hello-agent\n",
            stderr="",
        ),
    )

    execute_response = client.post(
        "/api/system/script/execute",
        json={
            "confirmation_id": preview_payload["confirmation_id"],
            "confirm": True,
        },
    )
    assert execute_response.status_code == 200
    execute_payload = execute_response.json()
    assert execute_payload["ok"] is True
    assert "hello-agent" in execute_payload["stdout"]
