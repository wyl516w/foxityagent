import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_studio.api.routes import build_router
from agent_studio.core.config import AppConfig
from agent_studio.core.models import (
    ChatRequest,
    ChatResponse,
    ElementLookupResponse,
    ElementMatch,
    OcrResponse,
    OcrTextLine,
    ProviderCapabilityProfile,
    ProviderSettingsPayload,
    ProviderType,
    ScreenshotResponse,
)
from agent_studio.core.state import SharedState
from agent_studio.services.automation.noop_controller import NoopInputController
from agent_studio.services.automation.permission_manager import PermissionManager
from agent_studio.services.conversation_service import ConversationService
from agent_studio.services.desktop import DesktopAgentRuntime
from agent_studio.services.system.system_service import SystemService
from agent_studio.services.workflows.workflow_service import WorkflowService
from agent_studio.storage.sqlite_store import SQLiteStore


def _make_test_dir() -> Path:
    base = Path(".testdata") / f"runtime-{uuid4().hex}"
    base.mkdir(parents=True, exist_ok=False)
    return base


class _StubPerceptionService:
    def __init__(self) -> None:
        self.capture_calls = 0

    def capture_screen(self):
        self.capture_calls += 1
        return ScreenshotResponse(
            ok=True,
            image_path="C:/tmp/runtime-capture.png",
            width=1280,
            height=720,
            message="Screenshot captured successfully.",
        )

    def run_ocr(self, image_path: str):
        return OcrResponse(
            ok=True,
            image_path=image_path,
            engine="stub",
            lines=[
                OcrTextLine(
                    text="Settings",
                    score=0.99,
                    bbox=[[10, 10], [110, 10], [110, 40], [10, 40]],
                )
            ],
            message="OCR completed with 1 text lines.",
        )

    def find_text(self, image_path: str, query: str, case_sensitive: bool = False):
        return ElementLookupResponse(
            ok=True,
            image_path=image_path,
            query=query,
            matches=[
                ElementMatch(
                    text="Settings",
                    score=0.99,
                    bbox=[[10, 10], [110, 10], [110, 40], [10, 40]],
                    center_x=40,
                    center_y=50,
                )
            ],
            message=f"Found 1 matches for '{query}'.",
        )


class _StubModelRouter:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.request_history: list[dict] = []

    async def chat(
        self,
        request: ChatRequest,
        *,
        settings_override: ProviderSettingsPayload | None = None,
        assignment=None,
    ) -> ChatResponse:
        if not self._responses:
            raise AssertionError("No stub responses remain.")
        self.request_history.append(request.model_dump(mode="json"))
        return ChatResponse(
            provider=ProviderType.MOCK,
            model="mock",
            content=self._responses.pop(0),
        )

    def describe_capabilities(self, settings=None):
        return type(
            "Capabilities",
            (),
            {
                "capabilities": [
                    ProviderCapabilityProfile(
                        provider=ProviderType.MOCK,
                        label="Mock",
                        supports_text=True,
                        default_model="mock",
                        local_runtime=True,
                        routing_hint="Mock route.",
                    )
                ]
            },
        )()


def test_chat_route_uses_desktop_runtime_for_image_attachments() -> None:
    test_dir = _make_test_dir()
    try:
        config = AppConfig(database_path=test_dir / "agent_studio.db")
        store = SQLiteStore(
            database_path=config.database_path,
            event_retention_limit=config.event_retention_limit,
        )
        store.initialize()
        state = SharedState(config=config, store=store)
        permission_manager = PermissionManager(state=state)
        input_controller = NoopInputController(
            state=state,
            permission_manager=permission_manager,
        )
        perception_service = _StubPerceptionService()
        model_router = _StubModelRouter(
            responses=[
                (
                    '{"summary":"Desktop looks ready.",'
                    '"recommended_action":{"kind":"move_mouse","text":"640,360","why":"Open the highlighted item.","confidence":0.82}}'
                )
            ]
        )
        desktop_runtime = DesktopAgentRuntime(
            state=state,
            perception_service=perception_service,
            input_controller=input_controller,
            model_router=model_router,
        )
        workflow_service = WorkflowService(
            store=store,
            state=state,
            perception_service=perception_service,
            input_controller=input_controller,
            permission_manager=permission_manager,
            system_service=SystemService(
                config=config,
                perception_service=perception_service,
                state=state,
                model_router=model_router,
            ),
            model_router=model_router,
        )

        app = FastAPI()
        app.include_router(
            build_router(
                config=config,
                state=state,
                model_router=model_router,
                permission_manager=permission_manager,
                input_controller=input_controller,
                conversation_service=ConversationService(store=store),
                perception_service=perception_service,
                desktop_runtime=desktop_runtime,
                workflow_service=workflow_service,
                system_service=SystemService(
                    config=config,
                    perception_service=perception_service,
                    state=state,
                    model_router=model_router,
                ),
            )
        )
        client = TestClient(app)
        source_image = test_dir / "capture.png"
        source_image.write_bytes(b"\x89PNG\r\n\x1a\nruntime-image")

        response = client.post(
            "/api/chat",
            json={
                "message": "Inspect this desktop screenshot and suggest the next action.",
                "attachments": [
                    {
                        "name": "capture.png",
                        "image_path": str(source_image),
                    }
                ],
            },
        )
        assert response.status_code == 200
        payload = response.json()

        assert payload["conversation_id"]
        assert payload["task_id"] is None
        assert payload["task_status"] is None
        assert payload["task_title"] is None
        assert "Desktop looks ready." in payload["content"]
        assert "move_mouse (640,360)" in payload["content"]
        assert payload["vision_used"] is True
        assert payload["attachment_count"] >= 1

        task_details = client.get(
            f"/api/conversations/{payload['conversation_id']}/tasks/details"
        )
        assert task_details.status_code == 200
        assert task_details.json()["tasks"] == []
        assert model_router.request_history
        assert (
            model_router.request_history[0]["attachments"][0]["image_path"]
            != str(source_image)
        )
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_chat_route_without_images_stays_normal_chat_even_with_workflow_service() -> None:
    test_dir = _make_test_dir()
    try:
        config = AppConfig(database_path=test_dir / "agent_studio.db")
        store = SQLiteStore(
            database_path=config.database_path,
            event_retention_limit=config.event_retention_limit,
        )
        store.initialize()
        state = SharedState(config=config, store=store)
        permission_manager = PermissionManager(state=state)
        input_controller = NoopInputController(
            state=state,
            permission_manager=permission_manager,
        )
        perception_service = _StubPerceptionService()
        model_router = _StubModelRouter(responses=["Direct chat answer from chat route."])
        desktop_runtime = DesktopAgentRuntime(
            state=state,
            perception_service=perception_service,
            input_controller=input_controller,
            model_router=model_router,
        )
        workflow_service = WorkflowService(
            store=store,
            state=state,
            perception_service=perception_service,
            input_controller=input_controller,
            permission_manager=permission_manager,
            system_service=SystemService(
                config=config,
                perception_service=perception_service,
                state=state,
                model_router=model_router,
            ),
            model_router=model_router,
        )

        app = FastAPI()
        app.include_router(
            build_router(
                config=config,
                state=state,
                model_router=model_router,
                permission_manager=permission_manager,
                input_controller=input_controller,
                conversation_service=ConversationService(store=store),
                perception_service=perception_service,
                desktop_runtime=desktop_runtime,
                workflow_service=workflow_service,
                system_service=SystemService(
                    config=config,
                    perception_service=perception_service,
                    state=state,
                    model_router=model_router,
                ),
            )
        )
        client = TestClient(app)

        response = client.post("/api/chat", json={"message": "Just answer normally."})
        assert response.status_code == 200
        payload = response.json()

        assert payload["content"] == "Direct chat answer from chat route."
        assert payload["task_id"] is None
        assert payload["task_status"] is None

        task_details = client.get(
            f"/api/conversations/{payload['conversation_id']}/tasks/details"
        )
        assert task_details.status_code == 200
        assert task_details.json()["tasks"] == []
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_runtime_step_route_returns_observation_and_recommended_action() -> None:
    test_dir = _make_test_dir()
    try:
        config = AppConfig(database_path=test_dir / "agent_studio.db")
        store = SQLiteStore(
            database_path=config.database_path,
            event_retention_limit=config.event_retention_limit,
        )
        store.initialize()
        state = SharedState(config=config, store=store)
        permission_manager = PermissionManager(state=state)
        input_controller = NoopInputController(
            state=state,
            permission_manager=permission_manager,
        )
        perception_service = _StubPerceptionService()
        model_router = _StubModelRouter(
            responses=[
                (
                    '{"summary":"The Settings button is visible.",'
                    '"recommended_action":{"kind":"move_mouse","text":"40,50","why":"Position the cursor over Settings.","confidence":0.9}}'
                )
            ]
        )
        desktop_runtime = DesktopAgentRuntime(
            state=state,
            perception_service=perception_service,
            input_controller=input_controller,
            model_router=model_router,
        )

        app = FastAPI()
        app.include_router(
            build_router(
                config=config,
                state=state,
                model_router=model_router,
                permission_manager=permission_manager,
                input_controller=input_controller,
                conversation_service=None,
                perception_service=perception_service,
                desktop_runtime=desktop_runtime,
            )
        )
        client = TestClient(app)

        response = client.post(
            "/api/agent/runtime/step",
            json={
                "goal": "Hover over the Settings button.",
                "image_path": "C:/tmp/runtime-capture.png",
                "auto_execute": False,
            },
        )
        assert response.status_code == 200
        payload = response.json()

        assert payload["ok"] is True
        assert payload["observation"]["image_path"] == "C:/tmp/runtime-capture.png"
        assert payload["observation"]["summary"] == "The Settings button is visible."
        assert payload["recommended_action"]["kind"] == "move_mouse"
        assert payload["recommended_action"]["text"] == "40,50"
        assert payload["recommended_action"]["why"] == "Position the cursor over Settings."
        assert payload["executed"] is False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_runtime_step_route_captures_screen_when_image_path_is_omitted() -> None:
    test_dir = _make_test_dir()
    try:
        config = AppConfig(database_path=test_dir / "agent_studio.db")
        store = SQLiteStore(
            database_path=config.database_path,
            event_retention_limit=config.event_retention_limit,
        )
        store.initialize()
        state = SharedState(config=config, store=store)
        permission_manager = PermissionManager(state=state)
        input_controller = NoopInputController(
            state=state,
            permission_manager=permission_manager,
        )
        perception_service = _StubPerceptionService()
        model_router = _StubModelRouter(
            responses=[
                (
                    '{"summary":"Captured the desktop and found the target.",'
                    '"recommended_action":{"kind":"left_click","why":"The cursor is already positioned.","confidence":0.65}}'
                )
            ]
        )
        desktop_runtime = DesktopAgentRuntime(
            state=state,
            perception_service=perception_service,
            input_controller=input_controller,
            model_router=model_router,
        )

        app = FastAPI()
        app.include_router(
            build_router(
                config=config,
                state=state,
                model_router=model_router,
                permission_manager=permission_manager,
                input_controller=input_controller,
                conversation_service=None,
                perception_service=perception_service,
                desktop_runtime=desktop_runtime,
            )
        )
        client = TestClient(app)

        response = client.post(
            "/api/agent/runtime/step",
            json={"goal": "Capture the current desktop and decide the next action."},
        )
        assert response.status_code == 200
        payload = response.json()

        assert payload["ok"] is True
        assert payload["observation"]["image_path"] == "C:/tmp/runtime-capture.png"
        assert payload["recommended_action"]["kind"] == "left_click"
        assert perception_service.capture_calls == 1
        assert model_router.request_history
        assert (
            model_router.request_history[0]["attachments"][0]["image_path"]
            == "C:/tmp/runtime-capture.png"
        )
        settings_payload = client.get("/api/settings").json()
        assert settings_payload["ui"]["latest_capture_path"] == "C:/tmp/runtime-capture.png"
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_delete_conversation_removes_associated_tasks() -> None:
    test_dir = _make_test_dir()
    try:
        config = AppConfig(database_path=test_dir / "agent_studio.db")
        store = SQLiteStore(
            database_path=config.database_path,
            event_retention_limit=config.event_retention_limit,
        )
        store.initialize()
        state = SharedState(config=config, store=store)
        permission_manager = PermissionManager(state=state)
        input_controller = NoopInputController(
            state=state,
            permission_manager=permission_manager,
        )
        perception_service = _StubPerceptionService()
        model_router = _StubModelRouter(responses=['{"status":"complete","summary":"Finished."}'])
        workflow_service = WorkflowService(
            store=store,
            state=state,
            perception_service=perception_service,
            input_controller=input_controller,
            permission_manager=permission_manager,
            system_service=SystemService(
                config=config,
                perception_service=perception_service,
                state=state,
                model_router=model_router,
            ),
            model_router=model_router,
        )

        app = FastAPI()
        app.include_router(
            build_router(
                config=config,
                state=state,
                model_router=model_router,
                permission_manager=permission_manager,
                input_controller=input_controller,
                conversation_service=ConversationService(store=store),
                perception_service=perception_service,
                workflow_service=workflow_service,
                system_service=SystemService(
                    config=config,
                    perception_service=perception_service,
                    state=state,
                    model_router=model_router,
                ),
            )
        )
        client = TestClient(app)

        created = client.post("/api/conversations", json={"title": "Task Container"})
        assert created.status_code == 200
        conversation_id = created.json()["conversation"]["conversation_id"]

        task_response = client.post(
            "/api/tasks",
            json={
                "conversation_id": conversation_id,
                "title": "Conversation task",
                "steps": [{"kind": "capture_screen", "label": "Capture"}],
            },
        )
        assert task_response.status_code == 200
        task_id = task_response.json()["task_id"]

        deleted = client.delete(f"/api/conversations/{conversation_id}")
        assert deleted.status_code == 200

        assert store.get_conversation_summary(conversation_id) is None
        assert store.get_task(task_id) is None
        assert store.list_tasks(conversation_id=conversation_id) == []
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
