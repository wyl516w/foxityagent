from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from agent_studio.core.models import (
    AutomationSettingsPayload,
    ChatImageAttachment,
    ConversationMessage,
    ConversationSummary,
    ProviderSettingsPayload,
    UiStatePayload,
    WorkflowAgentNode,
    WorkflowTaskDetail,
    WorkflowTaskSummary,
)


class SQLiteStore:
    def __init__(
        self,
        database_path: Path,
        event_retention_limit: int = 1000,
    ) -> None:
        self.database_path = Path(database_path)
        self.event_retention_limit = event_retention_limit
        self._lock = RLock()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA_SQL)
            self._ensure_column(
                connection,
                table_name="conversation_messages",
                column_name="attachments_json",
                definition="TEXT NOT NULL DEFAULT '[]'",
            )
            connection.commit()

    def load_provider_settings(
        self, defaults: ProviderSettingsPayload
    ) -> ProviderSettingsPayload:
        payload = self._load_json_setting("provider_settings")
        if payload is None:
            return defaults.model_copy(deep=True)
        return ProviderSettingsPayload.model_validate(payload)

    def save_provider_settings(self, payload: ProviderSettingsPayload) -> None:
        self._save_json_setting("provider_settings", payload.model_dump(mode="json"))

    def load_automation_settings(
        self, defaults: AutomationSettingsPayload
    ) -> AutomationSettingsPayload:
        payload = self._load_json_setting("automation_settings")
        if payload is None:
            return defaults.model_copy(deep=True)
        return AutomationSettingsPayload.model_validate(payload)

    def save_automation_settings(self, payload: AutomationSettingsPayload) -> None:
        self._save_json_setting("automation_settings", payload.model_dump(mode="json"))

    def load_ui_state(self, defaults: UiStatePayload) -> UiStatePayload:
        payload = self._load_json_setting("ui_state")
        if payload is None:
            return defaults.model_copy(deep=True)
        return UiStatePayload.model_validate(payload)

    def save_ui_state(self, payload: UiStatePayload) -> None:
        self._save_json_setting("ui_state", payload.model_dump(mode="json"))

    def append_event(self, message: str, event_type: str = "app") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO event_log (event_type, message, created_at)
                VALUES (?, ?, ?)
                """,
                (event_type, message, now),
            )
            self._prune_events(connection)
            connection.commit()

    def load_recent_events(self, limit: int) -> list[str]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT message, created_at
                FROM event_log
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [self._format_event(row["created_at"], row["message"]) for row in rows]

    def create_conversation(
        self,
        conversation_id: str,
        title: str,
    ) -> ConversationSummary:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO conversations (id, title, metadata_json, created_at, updated_at)
                VALUES (?, ?, '{}', ?, ?)
                """,
                (conversation_id, title, now, now),
            )
            connection.commit()
        return self.get_conversation_summary(conversation_id)

    def get_conversation_summary(self, conversation_id: str) -> ConversationSummary | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT c.id, c.title, c.created_at, c.updated_at, COUNT(m.id) AS message_count
                FROM conversations c
                LEFT JOIN conversation_messages m ON m.conversation_id = c.id
                WHERE c.id = ?
                GROUP BY c.id, c.title, c.created_at, c.updated_at
                """,
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_conversation_summary(row)

    def list_conversations(self, limit: int = 100) -> list[ConversationSummary]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT c.id, c.title, c.created_at, c.updated_at, COUNT(m.id) AS message_count
                FROM conversations c
                LEFT JOIN conversation_messages m ON m.conversation_id = c.id
                GROUP BY c.id, c.title, c.created_at, c.updated_at
                ORDER BY c.updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_conversation_summary(row) for row in rows]

    def update_conversation_title(self, conversation_id: str, title: str) -> ConversationSummary:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE conversations
                SET title = ?, updated_at = ?
                WHERE id = ?
                """,
                (title, now, conversation_id),
            )
            connection.commit()
        summary = self.get_conversation_summary(conversation_id)
        if summary is None:
            raise ValueError(f"Conversation {conversation_id} was not found.")
        return summary

    def append_conversation_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        attachments: list[ChatImageAttachment] | None = None,
    ) -> ConversationMessage:
        now = datetime.now(timezone.utc).isoformat()
        attachment_payload = [
            attachment.model_dump(mode="json", exclude={"image_base64"})
            for attachment in (attachments or [])
        ]
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO conversation_messages (
                    conversation_id,
                    role,
                    content,
                    attachments_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    role,
                    content,
                    json.dumps(attachment_payload, ensure_ascii=True),
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE conversations
                SET updated_at = ?
                WHERE id = ?
                """,
                (now, conversation_id),
            )
            connection.commit()
        return ConversationMessage(
            role=role,
            content=content,
            created_at=now,
            attachments=attachments or [],
        )

    def get_conversation_messages(self, conversation_id: str) -> list[ConversationMessage]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT role, content, created_at, attachments_json
                FROM conversation_messages
                WHERE conversation_id = ?
                ORDER BY id ASC
                """,
                (conversation_id,),
            ).fetchall()
        return [
            ConversationMessage(
                role=row["role"],
                content=row["content"],
                created_at=row["created_at"],
                attachments=[
                    ChatImageAttachment.model_validate(item)
                    for item in json.loads(row["attachments_json"] or "[]")
                    if isinstance(item, dict)
                ],
            )
            for row in rows
        ]

    def create_task(
        self,
        task_id: str,
        title: str,
        status: str,
        payload: dict,
    ) -> WorkflowTaskDetail:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO tasks (id, title, status, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    title,
                    status,
                    json.dumps(payload, ensure_ascii=True),
                    now,
                    now,
                ),
            )
            connection.commit()
        task = self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task {task_id} was not found after creation.")
        return task

    def update_task(
        self,
        task_id: str,
        *,
        status: str,
        payload: dict,
        title: str | None = None,
    ) -> WorkflowTaskDetail:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            if title is None:
                connection.execute(
                    """
                    UPDATE tasks
                    SET status = ?, payload_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        status,
                        json.dumps(payload, ensure_ascii=True),
                        now,
                        task_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE tasks
                    SET title = ?, status = ?, payload_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        title,
                        status,
                        json.dumps(payload, ensure_ascii=True),
                        now,
                        task_id,
                    ),
                )
            connection.commit()
        task = self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task {task_id} was not found.")
        return task

    def get_task(self, task_id: str) -> WorkflowTaskDetail | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, title, status, payload_json, created_at, updated_at
                FROM tasks
                WHERE id = ?
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_task_detail(row)

    def list_tasks(
        self,
        limit: int = 100,
        conversation_id: str | None = None,
    ) -> list[WorkflowTaskSummary]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, title, status, payload_json, created_at, updated_at
                FROM tasks
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        tasks = [self._row_to_task_summary(row) for row in rows]
        if conversation_id is None:
            return tasks
        return [task for task in tasks if task.conversation_id == conversation_id]

    def get_task_payload(self, task_id: str) -> dict | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM tasks
                WHERE id = ?
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["payload_json"])

    def _load_json_setting(self, key: str) -> dict | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_settings WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["value_json"])

    def _save_json_setting(self, key: str, value: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO app_settings (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=True), now),
            )
            connection.commit()

    def _prune_events(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM event_log
            WHERE id NOT IN (
                SELECT id
                FROM event_log
                ORDER BY id DESC
                LIMIT ?
            )
            """,
            (self.event_retention_limit,),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        *,
        table_name: str,
        column_name: str,
        definition: str,
    ) -> None:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        if any(row["name"] == column_name for row in rows):
            return
        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
        )

    @staticmethod
    def _format_event(created_at: str, message: str) -> str:
        parsed = datetime.fromisoformat(created_at)
        return f"[{parsed.astimezone().strftime('%H:%M:%S')}] {message}"

    @staticmethod
    def _row_to_conversation_summary(row: sqlite3.Row) -> ConversationSummary:
        return ConversationSummary(
            conversation_id=row["id"],
            title=row["title"],
            message_count=row["message_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_task_summary(row: sqlite3.Row) -> WorkflowTaskSummary:
        payload = json.loads(row["payload_json"])
        steps = payload.get("steps", [])
        results = payload.get("results", [])
        agent_records = payload.get("agents", [])
        last_message = None
        if results:
            last_message = results[-1].get("message")
        if last_message is None:
            last_message = payload.get("last_message")
        return WorkflowTaskSummary(
            task_id=row["id"],
            title=row["title"],
            status=row["status"],
            conversation_id=payload.get("conversation_id"),
            step_count=len(steps),
            agent_count=len(agent_records) if isinstance(agent_records, list) else 0,
            preferred_language=str(payload.get("preferred_language", "system")),
            last_message=last_message,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_task_detail(row: sqlite3.Row) -> WorkflowTaskDetail:
        payload = json.loads(row["payload_json"])
        return WorkflowTaskDetail(
            task_id=row["id"],
            title=row["title"],
            status=row["status"],
            conversation_id=payload.get("conversation_id"),
            preferred_language=str(payload.get("preferred_language", "system")),
            steps=payload.get("steps", []),
            results=payload.get("results", []),
            agents=SQLiteStore._build_agent_tree(payload.get("agents", [])),
            last_message=payload.get("last_message"),
            pending_approval=payload.get("pending_approval"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _build_agent_tree(records: list[dict]) -> list[WorkflowAgentNode]:
        if not isinstance(records, list):
            return []

        node_map: dict[str, dict] = {}
        roots: list[dict] = []

        for record in records:
            if not isinstance(record, dict):
                continue
            agent_id = str(record.get("agent_id", "")).strip()
            if not agent_id:
                continue
            node = dict(record)
            node["children"] = []
            node_map[agent_id] = node

        for agent_id, node in node_map.items():
            parent_id = node.get("parent_agent_id")
            if isinstance(parent_id, str) and parent_id and parent_id in node_map:
                node_map[parent_id].setdefault("children", []).append(node)
            else:
                roots.append(node)

        return [WorkflowAgentNode.model_validate(root) for root in roots]


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    attachments_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS permission_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    decision TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""
