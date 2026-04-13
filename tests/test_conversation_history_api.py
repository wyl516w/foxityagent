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
        assert history_payload["conversation"]["sandbox_dir"]
        assert [message["role"] for message in messages] == ["user", "assistant"]
        assert messages[0]["content"] == "remember this history"
        assert "Mock provider is active." in messages[1]["content"]

        settings_response = client.get("/api/settings")
        assert settings_response.status_code == 200
        settings_payload = settings_response.json()
        assert settings_payload["ui"]["current_conversation_id"] == conversation_id
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_chat_route_materializes_attachments_into_conversation_sandbox() -> None:
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

        source_image = test_dir / "outside.png"
        source_image.write_bytes(b"\x89PNG\r\n\x1a\nmock-image")

        chat_response = client.post(
            "/api/chat",
            json={
                "message": "save attachment into sandbox",
                "attachments": [
                    {
                        "name": "outside.png",
                        "media_type": "image/png",
                        "image_path": str(source_image),
                    }
                ],
            },
        )
        assert chat_response.status_code == 200
        conversation_id = chat_response.json()["conversation_id"]

        history_response = client.get(f"/api/conversations/{conversation_id}")
        assert history_response.status_code == 200
        payload = history_response.json()
        sandbox_dir = payload["conversation"]["sandbox_dir"]
        assert isinstance(sandbox_dir, str) and sandbox_dir

        user_message = payload["messages"][0]
        assert user_message["attachments"]
        stored_path = user_message["attachments"][0]["image_path"]
        assert isinstance(stored_path, str) and stored_path
        assert stored_path.startswith(sandbox_dir)
        assert Path(stored_path).exists()
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_delete_conversation_route_removes_conversation_from_database() -> None:
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

        created = client.post("/api/chat", json={"message": "delete this conversation"})
        assert created.status_code == 200
        conversation_id = created.json()["conversation_id"]

        deleted = client.delete(f"/api/conversations/{conversation_id}")
        assert deleted.status_code == 200
        assert deleted.json()["conversation_id"] == conversation_id
        assert deleted.json()["deleted"] is True

        history = client.get(f"/api/conversations/{conversation_id}")
        assert history.status_code == 404

        listed = client.get("/api/conversations")
        assert listed.status_code == 200
        assert all(
            item["conversation_id"] != conversation_id
            for item in listed.json()["conversations"]
        )
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
