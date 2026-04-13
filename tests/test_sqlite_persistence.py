import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

from agent_studio.core.config import AppConfig
from agent_studio.core.models import (
    AutomationSettingsPayload,
    ControlMode,
    OutputMode,
    ProviderSettingsPayload,
    ProviderType,
    UiStatePayload,
)
from agent_studio.core.state import SharedState
from agent_studio.storage.sqlite_store import SQLiteStore


def _make_test_dir() -> Path:
    base = Path(".testdata") / f"sqlite-{uuid4().hex}"
    base.mkdir(parents=True, exist_ok=False)
    return base


def test_sqlite_store_initializes_schema() -> None:
    test_dir = _make_test_dir()
    try:
        database_path = test_dir / "agent_studio.db"
        store = SQLiteStore(database_path=database_path, event_retention_limit=25)

        store.initialize()

        assert database_path.exists()
        with sqlite3.connect(database_path) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        assert "app_settings" in tables
        assert "event_log" in tables
        assert "conversations" in tables
        assert "tasks" in tables
        assert "permission_audit" in tables
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_shared_state_persists_settings_and_events() -> None:
    test_dir = _make_test_dir()
    try:
        config = AppConfig(
            database_path=test_dir / "agent_studio.db",
            recent_event_limit=10,
            event_retention_limit=50,
        )
        store = SQLiteStore(
            database_path=config.database_path,
            event_retention_limit=config.event_retention_limit,
        )
        store.initialize()

        first_state = SharedState(config=config, store=store)
        first_state.update_provider_settings(
            ProviderSettingsPayload(
                provider=ProviderType.OLLAMA,
                base_url="http://127.0.0.1:11434",
                model="qwen2.5:7b-instruct",
                timeout_seconds=60.0,
                allow_mock_fallback=False,
            )
        )
        first_state.update_automation_settings(
            AutomationSettingsPayload(control_mode=ControlMode.ALLOW_SESSION)
        )
        first_state.update_ui_state(UiStatePayload(current_conversation_id="conv-123"))
        first_state.update_ui_state(UiStatePayload(latest_capture_path="captures/latest.png"))
        first_state.update_ui_state(UiStatePayload(language="zh-CN"))
        first_state.update_ui_state(UiStatePayload(output_mode=OutputMode.STEP_SUMMARY))
        first_state.append_event("Persistence smoke event.")

        second_state = SharedState(config=config, store=store)

        provider = second_state.get_provider_settings()
        automation = second_state.get_automation_settings()
        ui_state = second_state.get_ui_state()
        events = second_state.get_recent_events()

        assert provider.provider == ProviderType.OLLAMA
        assert provider.model == "qwen2.5:7b-instruct"
        assert provider.allow_mock_fallback is False
        assert automation.control_mode == ControlMode.ALLOW_SESSION
        assert ui_state.current_conversation_id == "conv-123"
        assert ui_state.latest_capture_path == "captures/latest.png"
        assert ui_state.language == "zh-CN"
        assert ui_state.output_mode == OutputMode.STEP_SUMMARY
        assert any("Persistence smoke event." in event for event in events)

    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
