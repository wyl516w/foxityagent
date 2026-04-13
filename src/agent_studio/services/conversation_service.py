from __future__ import annotations

from uuid import uuid4

from agent_studio.core.models import (
    ChatImageAttachment,
    ConversationHistoryResponse,
    ConversationListResponse,
    ConversationSummary,
)
from agent_studio.storage.sqlite_store import SQLiteStore


class ConversationService:
    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    def create_conversation(self, title: str | None = None) -> ConversationSummary:
        conversation_id = uuid4().hex
        normalized_title = self._normalize_title(title) or "New Conversation"
        return self._store.create_conversation(conversation_id, normalized_title)

    def ensure_conversation(
        self,
        conversation_id: str | None,
        seed_message: str | None = None,
    ) -> ConversationSummary:
        if conversation_id:
            summary = self._store.get_conversation_summary(conversation_id)
            if summary is not None:
                if summary.message_count == 0 and seed_message:
                    title = self._normalize_title(seed_message)
                    if title and summary.title == "New Conversation":
                        return self._store.update_conversation_title(conversation_id, title)
                return summary

        title = self._normalize_title(seed_message) or "New Conversation"
        return self._store.create_conversation(uuid4().hex, title)

    def append_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        attachments: list[ChatImageAttachment] | None = None,
    ) -> None:
        self._store.append_conversation_message(
            conversation_id,
            role,
            content,
            attachments=attachments,
        )

    def list_conversations(self) -> ConversationListResponse:
        return ConversationListResponse(conversations=self._store.list_conversations())

    def get_history(self, conversation_id: str) -> ConversationHistoryResponse:
        summary = self._store.get_conversation_summary(conversation_id)
        if summary is None:
            raise ValueError(f"Conversation {conversation_id} was not found.")
        return ConversationHistoryResponse(
            conversation=summary,
            messages=self._store.get_conversation_messages(conversation_id),
        )

    @staticmethod
    def _normalize_title(title: str | None) -> str | None:
        if title is None:
            return None
        normalized = " ".join(title.strip().split())
        if not normalized:
            return None
        return normalized[:48]
