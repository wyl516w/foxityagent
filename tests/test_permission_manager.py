import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

from agent_studio.core.config import AppConfig
from agent_studio.core.models import AutomationSettingsPayload, ControlMode
from agent_studio.core.state import SharedState
from agent_studio.services.automation.permission_manager import PermissionManager
from agent_studio.storage.sqlite_store import SQLiteStore


def _make_test_dir() -> Path:
    base = Path(".testdata") / f"permission-{uuid4().hex}"
    base.mkdir(parents=True, exist_ok=False)
    return base


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


def test_permission_decisions_are_written_to_permission_audit() -> None:
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
            AutomationSettingsPayload(control_mode=ControlMode.ASK)
        )
        manager = PermissionManager(state=state)

        blocked = manager.evaluate("demo:left_click")
        manager.approve_once("demo:left_click")
        allowed = manager.evaluate("demo:left_click")

        assert blocked.requires_confirmation is True
        assert allowed.allowed is True

        with sqlite3.connect(config.database_path) as connection:
            rows = connection.execute(
                "SELECT action, decision FROM permission_audit ORDER BY id ASC"
            ).fetchall()

        assert [row[1] for row in rows] == ["ask", "approve_once", "allow_once"]
        assert all(row[0] == "demo:left_click" for row in rows)
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
