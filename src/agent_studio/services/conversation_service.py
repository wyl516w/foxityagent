from __future__ import annotations

import base64
from pathlib import Path
import shutil
from uuid import uuid4

from agent_studio.core.models import (
    ChatImageAttachment,
    DeleteConversationResponse,
    ConversationHistoryResponse,
    ConversationListResponse,
    ConversationMessage,
    ConversationSummary,
)
from agent_studio.storage.sqlite_store import SQLiteStore


class ConversationService:
    def __init__(
        self,
        store: SQLiteStore,
        *,
        conversations_root: Path | None = None,
    ) -> None:
        self._store = store
        self._conversations_root = (
            (conversations_root or self._store.database_path.parent / "conversations")
            .expanduser()
            .resolve()
        )
        self._conversations_root.mkdir(parents=True, exist_ok=True)

    def create_conversation(self, title: str | None = None) -> ConversationSummary:
        conversation_id = uuid4().hex
        normalized_title = self._normalize_title(title) or "New Conversation"
        sandbox_dir = self._ensure_conversation_sandbox_dir(conversation_id)
        return self._store.create_conversation(
            conversation_id,
            normalized_title,
            metadata={"sandbox_dir": str(sandbox_dir)},
        )

    def ensure_conversation(
        self,
        conversation_id: str | None,
        seed_message: str | None = None,
    ) -> ConversationSummary:
        if conversation_id:
            summary = self._store.get_conversation_summary(conversation_id)
            if summary is not None:
                self._ensure_summary_sandbox(summary)
                if summary.message_count == 0 and seed_message:
                    title = self._normalize_title(seed_message)
                    if title and summary.title == "New Conversation":
                        return self._store.update_conversation_title(conversation_id, title)
                return summary

        title = self._normalize_title(seed_message) or "New Conversation"
        new_conversation_id = uuid4().hex
        sandbox_dir = self._ensure_conversation_sandbox_dir(new_conversation_id)
        return self._store.create_conversation(
            new_conversation_id,
            title,
            metadata={"sandbox_dir": str(sandbox_dir)},
        )

    def append_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        attachments: list[ChatImageAttachment] | None = None,
        linked_task_id: str | None = None,
    ) -> ConversationMessage:
        return self._store.append_conversation_message(
            conversation_id,
            role,
            content,
            attachments=attachments,
            linked_task_id=linked_task_id,
        )

    def link_message_to_task(self, message_id: str, task_id: str) -> None:
        self._store.update_conversation_message_task_link(
            message_id=message_id,
            task_id=task_id,
        )

    def list_conversations(self) -> ConversationListResponse:
        return ConversationListResponse(conversations=self._store.list_conversations())

    def get_history(self, conversation_id: str) -> ConversationHistoryResponse:
        summary = self._store.get_conversation_summary(conversation_id)
        if summary is None:
            raise ValueError(f"Conversation {conversation_id} was not found.")
        summary = self._ensure_summary_sandbox(summary)
        return ConversationHistoryResponse(
            conversation=summary,
            messages=self._store.get_conversation_messages(conversation_id),
        )

    def delete_conversation(self, conversation_id: str) -> DeleteConversationResponse:
        summary = self._store.get_conversation_summary(conversation_id)
        if summary is None:
            raise ValueError(f"Conversation {conversation_id} was not found.")
        summary = self._ensure_summary_sandbox(summary)
        self._store.delete_tasks_for_conversation(conversation_id)
        deleted = self._store.delete_conversation(conversation_id)
        if not deleted:
            raise ValueError(f"Conversation {conversation_id} was not found.")
        if summary.sandbox_dir:
            self._safe_remove_sandbox_dir(Path(summary.sandbox_dir))
        return DeleteConversationResponse(conversation_id=conversation_id)

    def materialize_attachments_for_conversation(
        self,
        *,
        conversation_id: str,
        attachments: list[ChatImageAttachment],
    ) -> list[ChatImageAttachment]:
        if not attachments:
            return []
        sandbox_dir = self.get_conversation_sandbox_dir(conversation_id)
        upload_dir = sandbox_dir / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        normalized: list[ChatImageAttachment] = []
        for attachment in attachments:
            if attachment.image_path:
                source_path = Path(attachment.image_path).expanduser()
                if not source_path.exists():
                    raise ValueError(f"Attachment file was not found: {source_path}")
                if source_path.is_dir():
                    raise ValueError(f"Attachment path must be a file: {source_path}")
                target_name = _safe_filename(attachment.name or source_path.name)
                target_path = upload_dir / f"{uuid4().hex[:12]}-{target_name}"
                shutil.copy2(source_path, target_path)
                normalized.append(
                    attachment.model_copy(
                        update={
                            "name": attachment.name or source_path.name,
                            "image_path": str(target_path.resolve()),
                            "image_base64": None,
                        }
                    )
                )
                continue
            if attachment.image_base64:
                payload = attachment.image_base64.strip()
                if payload.startswith("data:") and "," in payload:
                    _, payload = payload.split(",", 1)
                target_name = _safe_filename(
                    attachment.name or f"inline-image{_suffix_from_media_type(attachment.media_type)}"
                )
                target_path = upload_dir / f"{uuid4().hex[:12]}-{target_name}"
                try:
                    target_path.write_bytes(base64.b64decode(payload, validate=False))
                except Exception as exc:
                    raise ValueError(f"Attachment base64 decode failed: {exc}") from exc
                normalized.append(
                    attachment.model_copy(
                        update={
                            "name": attachment.name or target_name,
                            "image_path": str(target_path.resolve()),
                            "image_base64": None,
                        }
                    )
                )
                continue
            normalized.append(attachment)
        return normalized

    def get_conversation_sandbox_dir(self, conversation_id: str) -> Path:
        summary = self._store.get_conversation_summary(conversation_id)
        if summary is None:
            raise ValueError(f"Conversation {conversation_id} was not found.")
        summary = self._ensure_summary_sandbox(summary)
        if not summary.sandbox_dir:
            raise ValueError(f"Conversation {conversation_id} does not have a sandbox directory.")
        return Path(summary.sandbox_dir)

    def _ensure_summary_sandbox(self, summary: ConversationSummary) -> ConversationSummary:
        if summary.sandbox_dir:
            sandbox_path = Path(summary.sandbox_dir).expanduser().resolve()
        else:
            sandbox_path = self._conversation_sandbox_dir(summary.conversation_id)
            summary = self._store.update_conversation_metadata(
                summary.conversation_id,
                {"sandbox_dir": str(sandbox_path)},
            )
        sandbox_path.mkdir(parents=True, exist_ok=True)
        return summary

    def _ensure_conversation_sandbox_dir(self, conversation_id: str) -> Path:
        sandbox_path = self._conversation_sandbox_dir(conversation_id)
        sandbox_path.mkdir(parents=True, exist_ok=True)
        return sandbox_path

    def _conversation_sandbox_dir(self, conversation_id: str) -> Path:
        return (self._conversations_root / conversation_id).resolve()

    def _safe_remove_sandbox_dir(self, sandbox_dir: Path) -> None:
        resolved_root = self._conversations_root.resolve()
        resolved_target = sandbox_dir.expanduser().resolve()
        if not resolved_target.is_relative_to(resolved_root):
            return
        if resolved_target.exists():
            shutil.rmtree(resolved_target, ignore_errors=True)

    @staticmethod
    def _normalize_title(title: str | None) -> str | None:
        if title is None:
            return None
        normalized = " ".join(title.strip().split())
        if not normalized:
            return None
        return normalized[:48]


def _safe_filename(value: str) -> str:
    stripped = Path(value).name.strip()
    if not stripped:
        return "attachment.png"
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in stripped)
    return safe[:120] or "attachment.png"


def _suffix_from_media_type(media_type: str | None) -> str:
    value = (media_type or "").lower()
    if value == "image/jpeg":
        return ".jpg"
    if value.startswith("image/"):
        return f".{value.split('/', 1)[1]}"
    return ".png"
