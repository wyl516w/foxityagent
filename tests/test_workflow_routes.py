import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_studio.api.routes import build_router
from agent_studio.core.config import AppConfig
from agent_studio.core.models import (
    AutomationSettingsPayload,
    ChatRequest,
    ChatResponse,
    ControlMode,
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
from agent_studio.services.model_router import ModelRouter
from agent_studio.services.system.system_service import SystemService
from agent_studio.services.workflows.workflow_service import WorkflowService
from agent_studio.storage.sqlite_store import SQLiteStore


def _make_test_dir() -> Path:
    base = Path(".testdata") / f"workflow-{uuid4().hex}"
    base.mkdir(parents=True, exist_ok=False)
    return base


class _StubPerceptionService:
    def capture_screen(self):
        return ScreenshotResponse(
            ok=True,
            image_path="C:/tmp/workflow-capture.png",
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
                    text="Open Settings",
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
                    text="Open Settings",
                    score=0.99,
                    bbox=[[10, 10], [110, 10], [110, 40], [10, 40]],
                    center_x=40,
                    center_y=50,
                )
            ],
            message=f"Found 1 matches for '{query}'.",
        )


class _StubAutonomousModelRouter:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.settings_history: list[dict | None] = []

    async def chat(
        self,
        request: ChatRequest,
        *,
        settings_override: ProviderSettingsPayload | None = None,
        assignment=None,
    ) -> ChatResponse:
        if not self._responses:
            raise AssertionError("No stub planner responses remain.")
        self.settings_history.append(
            settings_override.model_dump(mode="json") if settings_override else None
        )
        return ChatResponse(
            provider=ProviderType.MOCK,
            model="mock",
            content=self._responses.pop(0),
        )

    def resolve_settings(
        self,
        *,
        base: ProviderSettingsPayload | None = None,
        assignment=None,
    ) -> ProviderSettingsPayload:
        settings = (base or ProviderSettingsPayload()).model_copy(deep=True)
        if not isinstance(assignment, dict):
            return settings
        provider = assignment.get("provider")
        model = assignment.get("model")
        base_url = assignment.get("base_url")
        updates: dict[str, object] = {}
        if provider:
            updates["provider"] = ProviderType(provider)
            if provider == ProviderType.OLLAMA.value:
                updates["base_url"] = "http://127.0.0.1:11434"
                updates["model"] = "qwen3-vl:4b"
            elif provider == ProviderType.MOCK.value:
                updates["base_url"] = "mock://local"
                updates["model"] = "mock"
        if model:
            updates["model"] = model
        if base_url:
            updates["base_url"] = base_url
        return settings.model_copy(update=updates)

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
                    ),
                    ProviderCapabilityProfile(
                        provider=ProviderType.OPENAI_COMPATIBLE,
                        label="OpenAI Compatible",
                        supports_text=True,
                        supports_vision=True,
                        remote_runtime=True,
                        default_model="gpt-4.1-mini",
                        routing_hint="Remote route.",
                    ),
                    ProviderCapabilityProfile(
                        provider=ProviderType.OLLAMA,
                        label="Ollama",
                        supports_text=True,
                        supports_vision=True,
                        local_runtime=True,
                        default_model="qwen3-vl:4b",
                        routing_hint="Local route.",
                    ),
                ]
            },
        )()


def test_apply_settings_route_updates_language_without_clobbering_ui_state() -> None:
    test_dir = _make_test_dir()
    try:
        config = AppConfig(database_path=test_dir / "agent_studio.db")
        store = SQLiteStore(
            database_path=config.database_path,
            event_retention_limit=config.event_retention_limit,
        )
        store.initialize()
        state = SharedState(config=config, store=store)
        state.update_ui_state(
            payload=type(state.get_ui_state())(
                current_conversation_id="conv-7",
                latest_capture_path="capture.png",
            )
        )
        permission_manager = PermissionManager(state=state)
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
                perception_service=None,
                workflow_service=None,
            )
        )
        client = TestClient(app)

        response = client.post("/api/settings/apply", json={"ui": {"language": "fr-FR"}})
        assert response.status_code == 200

        response = client.post(
            "/api/settings/apply",
            json={"ui": {"language": "fr-FR", "output_mode": "step_summary"}},
        )

        assert response.status_code == 200
        ui_payload = response.json()["ui"]
        assert ui_payload["language"] == "fr-FR"
        assert ui_payload["current_conversation_id"] == "conv-7"
        assert ui_payload["latest_capture_path"] == "capture.png"
        assert ui_payload["output_mode"] == "step_summary"
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_chat_route_creates_autonomous_task_when_workflow_service_is_enabled() -> None:
    test_dir = _make_test_dir()
    try:
        config = AppConfig(database_path=test_dir / "agent_studio.db")
        store = SQLiteStore(
            database_path=config.database_path,
            event_retention_limit=config.event_retention_limit,
        )
        store.initialize()
        state = SharedState(config=config, store=store)
        state.update_provider_settings(
            ProviderSettingsPayload(
                provider=ProviderType.OPENAI_COMPATIBLE,
                base_url="https://api.example.test/v1",
                model="gpt-4.1-mini",
                allow_mock_fallback=False,
            )
        )
        permission_manager = PermissionManager(state=state)
        input_controller = NoopInputController(
            state=state,
            permission_manager=permission_manager,
        )
        perception_service = _StubPerceptionService()
        model_router = _StubAutonomousModelRouter(
            responses=[
                '{"status":"continue","summary":"Capture the current screen.","action":{"kind":"capture_screen"}}',
                '{"status":"complete","summary":"Captured the current screen and finished."}',
            ]
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

        response = client.post(
            "/api/chat",
            json={"message": "Take a look at the current desktop and report back."},
        )
        assert response.status_code == 200
        payload = response.json()

        assert payload["conversation_id"]
        assert payload["task_id"]
        assert payload["task_status"] == "completed"
        assert payload["task_title"]
        assert payload["content"]
        assert any(
            keyword in payload["content"].lower()
            for keyword in ("completed", "finished")
        )

        history = client.get(f"/api/conversations/{payload['conversation_id']}")
        assert history.status_code == 200
        history_payload = history.json()
        assert [message["role"] for message in history_payload["messages"]] == [
            "user",
            "assistant",
        ]

        task_details = client.get(
            f"/api/conversations/{payload['conversation_id']}/tasks/details"
        )
        assert task_details.status_code == 200
        detail_payload = task_details.json()
        assert detail_payload["tasks"][0]["task_id"] == payload["task_id"]
        assert model_router.settings_history[0]["provider"] == "openai_compatible"
        assert model_router.settings_history[0]["model"] == "gpt-4.1-mini"
        assert model_router.settings_history[0]["base_url"] == "https://api.example.test/v1"
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_workflow_task_routes_create_and_run_tasks() -> None:
    test_dir = _make_test_dir()
    try:
        config = AppConfig(database_path=test_dir / "agent_studio.db")
        store = SQLiteStore(
            database_path=config.database_path,
            event_retention_limit=config.event_retention_limit,
        )
        store.initialize()
        state = SharedState(config=config, store=store)
        state.update_automation_settings(
            AutomationSettingsPayload(control_mode=ControlMode.ALLOW_SESSION)
        )
        permission_manager = PermissionManager(state=state)
        input_controller = NoopInputController(
            state=state,
            permission_manager=permission_manager,
        )
        perception_service = _StubPerceptionService()
        workflow_service = WorkflowService(
            store=store,
            state=state,
            perception_service=perception_service,
            input_controller=input_controller,
            permission_manager=permission_manager,
            system_service=SystemService(
                config=config,
                perception_service=perception_service,
            ),
        )

        app = FastAPI()
        app.include_router(
            build_router(
                config=config,
                state=state,
                model_router=ModelRouter(config=config, state=state),
                permission_manager=permission_manager,
                input_controller=input_controller,
                conversation_service=None,
                perception_service=perception_service,
                workflow_service=workflow_service,
            )
        )
        client = TestClient(app)

        create_response = client.post(
            "/api/tasks",
            json={
                "title": "Open settings",
                "preferred_language": "ja-JP",
                "steps": [
                    {"kind": "detect_system", "label": "Detect Host"},
                    {"kind": "capture_screen", "label": "Capture"},
                    {"kind": "find_text", "text": "Settings"},
                    {"kind": "move_mouse"},
                ],
            },
        )
        assert create_response.status_code == 200
        task_payload = create_response.json()
        task_id = task_payload["task_id"]
        assert task_payload["status"] == "draft"
        assert task_payload["preferred_language"] == "ja-JP"
        assert len(task_payload["agents"]) == 1
        root_agent_id = task_payload["agents"][0]["agent_id"]

        child_response = client.post(
            f"/api/tasks/{task_id}/agents",
            json={
                "name": "Child Agent",
                "parent_agent_id": root_agent_id,
                "steps": [{"kind": "type_text", "text": "confirm"}],
            },
        )
        assert child_response.status_code == 200
        child_task_payload = child_response.json()
        child_agent_id = child_task_payload["agents"][0]["children"][0]["agent_id"]

        grandchild_response = client.post(
            f"/api/tasks/{task_id}/agents",
            json={
                "name": "Grandchild Agent",
                "parent_agent_id": child_agent_id,
                "steps": [{"kind": "left_click"}],
            },
        )
        assert grandchild_response.status_code == 200

        tree_response = client.get(f"/api/tasks/{task_id}/agents/tree")
        assert tree_response.status_code == 200
        tree_payload = tree_response.json()
        assert len(tree_payload["agents"]) == 1
        assert tree_payload["agents"][0]["children"][0]["name"] == "Child Agent"
        assert (
            tree_payload["agents"][0]["children"][0]["children"][0]["name"]
            == "Grandchild Agent"
        )

        run_response = client.post(f"/api/tasks/{task_id}/run")
        assert run_response.status_code == 200
        run_payload = run_response.json()["task"]
        assert run_payload["status"] == "completed"
        assert len(run_payload["results"]) == 6
        assert run_payload["results"][0]["output"]["os_name"]
        assert run_payload["results"][2]["output"]["matches"][0]["center_x"] == 40
        assert run_payload["results"][3]["output"]["coordinates"] == "40,50"
        assert run_payload["results"][4]["agent_name"] == "Child Agent"
        assert run_payload["results"][5]["agent_name"] == "Grandchild Agent"
        assert run_payload["agents"][0]["children"][0]["status"] == "completed"
        assert (
            run_payload["agents"][0]["children"][0]["children"][0]["status"]
            == "completed"
        )

        list_response = client.get("/api/tasks")
        assert list_response.status_code == 200
        assert list_response.json()["tasks"][0]["task_id"] == task_id
        assert list_response.json()["tasks"][0]["status"] == "completed"
        assert list_response.json()["tasks"][0]["agent_count"] == 3
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_workflow_execute_script_waits_for_approval_and_resumes(monkeypatch) -> None:
    test_dir = _make_test_dir()
    try:
        config = AppConfig(database_path=test_dir / "agent_studio.db")
        store = SQLiteStore(
            database_path=config.database_path,
            event_retention_limit=config.event_retention_limit,
        )
        store.initialize()
        state = SharedState(config=config, store=store)
        state.update_automation_settings(
            AutomationSettingsPayload(control_mode=ControlMode.ALLOW_SESSION)
        )
        permission_manager = PermissionManager(state=state)
        input_controller = NoopInputController(
            state=state,
            permission_manager=permission_manager,
        )
        perception_service = _StubPerceptionService()
        system_service = SystemService(
            config=config,
            perception_service=perception_service,
        )
        workflow_service = WorkflowService(
            store=store,
            state=state,
            perception_service=perception_service,
            input_controller=input_controller,
            permission_manager=permission_manager,
            system_service=system_service,
        )

        monkeypatch.setattr(
            system_service,
            "execute_prepared_script",
            lambda request: type(
                "StubResult",
                (),
                {
                    "ok": True,
                    "confirmation_id": request.confirmation_id,
                    "runtime": type("Runtime", (), {"value": "python"})(),
                    "preferred_shell": "python.exe",
                    "exit_code": 0,
                    "timed_out": False,
                    "stdout": "approved\n",
                    "stderr": "",
                    "summary": "Script executed successfully.",
                },
            )(),
        )

        app = FastAPI()
        app.include_router(
            build_router(
                config=config,
                state=state,
                model_router=ModelRouter(config=config, state=state),
                permission_manager=permission_manager,
                input_controller=input_controller,
                conversation_service=None,
                perception_service=perception_service,
                workflow_service=workflow_service,
                system_service=system_service,
            )
        )
        client = TestClient(app)

        create_response = client.post(
            "/api/tasks",
            json={
                "title": "Reviewed script task",
                "steps": [
                    {"kind": "detect_system"},
                    {"kind": "execute_script", "text": "print('hello from task')"},
                    {"kind": "type_text", "text": "done"},
                ],
            },
        )
        assert create_response.status_code == 200
        task_id = create_response.json()["task_id"]

        run_response = client.post(f"/api/tasks/{task_id}/run")
        assert run_response.status_code == 200
        run_payload = run_response.json()["task"]
        assert run_payload["status"] == "waiting_approval"
        assert run_payload["pending_approval"]["confirmation_id"].startswith("script-")
        assert len(run_payload["results"]) == 1

        approve_response = client.post(
            f"/api/tasks/{task_id}/approve",
            json={"decision": "allow"},
        )
        assert approve_response.status_code == 200
        approved_payload = approve_response.json()["task"]
        assert approved_payload["status"] == "completed"
        assert approved_payload["pending_approval"] is None
        assert len(approved_payload["results"]) == 3
        assert approved_payload["results"][1]["kind"] == "execute_script"
        assert approved_payload["results"][1]["output"]["stdout"] == "approved\n"
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_autonomous_task_runs_without_seed_steps_and_can_delegate() -> None:
    test_dir = _make_test_dir()
    try:
        config = AppConfig(database_path=test_dir / "agent_studio.db")
        store = SQLiteStore(
            database_path=config.database_path,
            event_retention_limit=config.event_retention_limit,
        )
        store.initialize()
        state = SharedState(config=config, store=store)
        state.update_automation_settings(
            AutomationSettingsPayload(control_mode=ControlMode.ALLOW_SESSION)
        )
        permission_manager = PermissionManager(state=state)
        input_controller = NoopInputController(
            state=state,
            permission_manager=permission_manager,
        )
        perception_service = _StubPerceptionService()
        stub_router = _StubAutonomousModelRouter(
            [
                (
                    '{"status":"delegate","summary":"Let a local vision agent inspect the UI.",'
                    '"delegate":{"name":"Vision Agent","instruction":"Inspect the dialog and report the next action.",'
                    '"max_iterations":2,"provider":"ollama","model":"qwen3-vl:4b",'
                    '"base_url":"http://127.0.0.1:11434",'
                    '"assignment_reason":"Use the local multimodal model for UI understanding."}}'
                ),
                '{"status":"complete","summary":"The root agent has finished planning."}',
                '{"status":"complete","summary":"The child agent is done."}',
            ]
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
            ),
            model_router=stub_router,
        )

        app = FastAPI()
        app.include_router(
            build_router(
                config=config,
                state=state,
                model_router=ModelRouter(config=config, state=state),
                permission_manager=permission_manager,
                input_controller=input_controller,
                conversation_service=None,
                perception_service=perception_service,
                workflow_service=workflow_service,
                system_service=SystemService(
                    config=config,
                    perception_service=perception_service,
                ),
            )
        )
        client = TestClient(app)

        create_response = client.post(
            "/api/tasks",
            json={
                "instruction": "Open settings and confirm the current dialog.",
                "steps": [],
                "autonomous": True,
                "max_iterations": 6,
            },
        )
        assert create_response.status_code == 200
        created_task = create_response.json()
        task_id = created_task["task_id"]
        assert created_task["agents"][0]["autonomous"] is True
        assert created_task["steps"] == []

        run_response = client.post(f"/api/tasks/{task_id}/run")
        assert run_response.status_code == 200
        task_payload = run_response.json()["task"]
        assert task_payload["status"] == "completed"
        assert len(task_payload["agents"]) == 1
        root_agent = task_payload["agents"][0]
        assert root_agent["children"][0]["name"] == "Vision Agent"
        assert root_agent["children"][0]["status"] == "completed"
        assert root_agent["children"][0]["model_assignment"]["provider"] == "ollama"
        assert root_agent["children"][0]["model_assignment"]["model"] == "qwen3-vl:4b"
        assert (
            root_agent["children"][0]["model_assignment"]["assignment_reason"]
            == "Use the local multimodal model for UI understanding."
        )
        assert any(result["kind"] == "delegate_agent" for result in task_payload["results"])
        assert any(result["kind"] == "complete" for result in task_payload["results"])
        delegate_result = next(
            result for result in task_payload["results"] if result["kind"] == "delegate_agent"
        )
        assert delegate_result["output"]["model_assignment"]["provider"] == "ollama"
        assert stub_router.settings_history[-1]["provider"] == "ollama"
        assert stub_router.settings_history[-1]["model"] == "qwen3-vl:4b"
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
