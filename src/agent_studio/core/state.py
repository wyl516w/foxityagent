from __future__ import annotations

from collections import deque
from datetime import datetime
from threading import RLock

from agent_studio.core.config import AppConfig
from agent_studio.core.models import (
    AutomationSettingsPayload,
    ProviderSettingsPayload,
    ProviderType,
    UiStatePayload,
)
from agent_studio.storage.sqlite_store import SQLiteStore


class SharedState:
    def __init__(self, config: AppConfig, store: SQLiteStore | None = None) -> None:
        self._config = config
        self._store = store
        self._lock = RLock()
        default_provider = ProviderSettingsPayload(
            provider=ProviderType.MOCK,
            base_url=config.openai_base_url,
            model=config.default_remote_model,
            timeout_seconds=config.request_timeout_seconds,
        )
        default_automation = AutomationSettingsPayload()
        default_ui = UiStatePayload()

        if self._store is None:
            self._provider = default_provider
            self._automation = default_automation
            self._ui = default_ui
            persisted_events: list[str] = []
        else:
            self._provider = self._store.load_provider_settings(default_provider)
            self._automation = self._store.load_automation_settings(default_automation)
            self._ui = self._store.load_ui_state(default_ui)
            persisted_events = self._store.load_recent_events(config.recent_event_limit)

        self._recent_events: deque[str] = deque(
            persisted_events,
            maxlen=config.recent_event_limit,
        )
        self.append_event("Application state initialized.")

    def get_provider_settings(self) -> ProviderSettingsPayload:
        with self._lock:
            return self._provider.model_copy(deep=True)

    def update_provider_settings(self, payload: ProviderSettingsPayload) -> ProviderSettingsPayload:
        with self._lock:
            self._provider = payload.model_copy(deep=True)
            if self._store is not None:
                self._store.save_provider_settings(self._provider)
            self._append_event_locked(f"Provider updated to {payload.provider.value}.")
            return self._provider.model_copy(deep=True)

    def get_automation_settings(self) -> AutomationSettingsPayload:
        with self._lock:
            return self._automation.model_copy(deep=True)

    def update_automation_settings(
        self, payload: AutomationSettingsPayload
    ) -> AutomationSettingsPayload:
        with self._lock:
            self._automation = payload.model_copy(deep=True)
            if self._store is not None:
                self._store.save_automation_settings(self._automation)
            self._append_event_locked(f"Control mode set to {payload.control_mode.value}.")
            return self._automation.model_copy(deep=True)

    def get_ui_state(self) -> UiStatePayload:
        with self._lock:
            return self._ui.model_copy(deep=True)

    def update_ui_state(self, payload: UiStatePayload) -> UiStatePayload:
        with self._lock:
            updated_ui = self._ui.model_copy(
                update=payload.model_dump(exclude_unset=True)
            )
            if updated_ui == self._ui:
                return self._ui.model_copy(deep=True)
            self._ui = updated_ui
            if self._store is not None:
                self._store.save_ui_state(self._ui)
            return self._ui.model_copy(deep=True)

    def get_recent_events(self) -> list[str]:
        with self._lock:
            return list(self._recent_events)

    @property
    def store(self) -> SQLiteStore | None:
        return self._store

    def append_event(self, message: str) -> None:
        with self._lock:
            self._append_event_locked(message)

    def _append_event_locked(self, message: str) -> None:
        if self._store is not None:
            formatted = self._store.append_event(message)
        else:
            formatted = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
        self._recent_events.appendleft(formatted)
