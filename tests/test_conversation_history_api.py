import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_studio.api.routes import build_router
from agent_studio.core.config import AppConfig
from agent_studio.core.state import SharedState
from agent_studio.services.automation.noop_controller import NoopInputController
from agent_studio.services.automation.permission_manager import PermissionManager
from agent_studio.services.conversation_service import ConversationService
from agent_studio.services.model_router import ModelRouter
from agent_studio.storage.sqlite_store import SQLiteStore


def _make_test_dir() -> Path:
    base = Path(".testdata") / f"conversation-{uuid4().hex}"
    base.mkdir(parents=True, exist_ok=False)
    return base


def test_chat_route_persists_conversation_history_and_ui_state() -> None:
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
                conversation_service=ConversationService(store=store),
                perception_service=None,
            )
        )
        client = TestClient(app)

        chat_response = client.post("/api/chat", json={"message": "remember this history"})
        assert chat_response.status_code == 200
        chat_payload = chat_response.json()
        conversation_id = chat_payload["conversation_id"]

        history_response = client.get(f"/api/conversations/{conversation_id}")
        assert history_response.status_code == 200
        history_payload = history_response.json()
        messages = history_payload["messages"]

        assert history_payload["conversation"]["conversation_id"] == conversation_id
        assert history_payload["conversation"]["title"].startswith("remember this history")
        assert [message["role"] for message in messages] == ["user", "assistant"]
        assert messages[0]["content"] == "remember this history"
        assert "Mock provider is active." in messages[1]["content"]

        settings_response = client.get("/api/settings")
        assert settings_response.status_code == 200
        settings_payload = settings_response.json()
        assert settings_payload["ui"]["current_conversation_id"] == conversation_id
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
