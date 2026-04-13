from __future__ import annotations

from dataclasses import dataclass
from html import escape
import mimetypes
from pathlib import Path
from urllib.parse import quote

import httpx
from PySide6 import QtCore, QtGui, QtWidgets

from agent_studio.core.config import AppConfig
from agent_studio.core.models import ApprovalTimeoutAction, ControlMode, OutputMode
from agent_studio.core.state import SharedState
from agent_studio.services.backend_server import BackendServer
from agent_studio.ui.internal_links import (
    MESSAGE_LINK_SCHEME,
    TASK_LINK_SCHEME,
    build_internal_link,
    message_anchor_name,
    parse_internal_link,
)
from agent_studio.ui.i18n import SYSTEM_LANGUAGE, translate
from agent_studio.ui.settings_dialog import SettingsDialog


DEFAULT_APPROVAL_TIMEOUT_PROMPT = (
    "Approval timed out. Continue with a safer alternative and avoid the blocked high-risk action."
)


class ApiWorker(QtCore.QThread):
    succeeded = QtCore.Signal(str, dict)
    failed = QtCore.Signal(str, str)

    def __init__(
        self,
        *,
        request_key: str,
        base_url: str,
        path: str,
        method: str = "GET",
        payload: dict | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__()
        self.request_key = request_key
        self.base_url = base_url
        self.path = path
        self.method = method
        self.payload = payload
        self.timeout = timeout

    def run(self) -> None:
        try:
            request_kwargs: dict[str, object] = {}
            if self.payload is not None:
                request_kwargs["json"] = self.payload
            with httpx.Client(timeout=self.timeout) as client:
                response = client.request(
                    self.method,
                    f"{self.base_url}{self.path}",
                    **request_kwargs,
                )
                response.raise_for_status()
                data = response.json() if response.content else {}
                self.succeeded.emit(self.request_key, data)
        except Exception as exc:  # pragma: no cover - UI pathway
            self.failed.emit(self.request_key, str(exc))


@dataclass
class TaskPageRefs:
    widget: QtWidgets.QWidget
    summary_view: QtWidgets.QTextBrowser
    agent_tabs: QtWidgets.QTabWidget


class MainWindow(QtWidgets.QMainWindow):
    def __init__(
        self,
        config: AppConfig,
        state: SharedState,
        backend: BackendServer,
    ) -> None:
        super().__init__()
        self.config = config
        self.state = state
        self.backend = backend

        self.system_language = QtCore.QLocale.system().name()
        self.current_language = SYSTEM_LANGUAGE
        self.output_mode = OutputMode.FINAL_ONLY.value
        self.current_conversation_id: str | None = None
        self.loaded_conversation_id: str | None = None
        self.selected_task_id: str | None = None
        self.latest_capture_path: str | None = None
        self.approval_timeout_seconds = 60
        self.approval_timeout_action = ApprovalTimeoutAction.DENY.value
        self.approval_timeout_prompt = DEFAULT_APPROVAL_TIMEOUT_PROMPT

        self._workers: dict[str, ApiWorker] = {}
        self._task_pages: dict[str, TaskPageRefs] = {}
        self._task_payloads: dict[str, dict] = {}
        self._selected_chat_attachments: list[str] = []
        self._loaded_conversation_payload: dict | None = None
        self._highlighted_message_id: str | None = None
        self._pending_task_link_id: str | None = None
        self._last_settings_payload = self._snapshot_from_state()
        self._active_settings_dialog: SettingsDialog | None = None
        self._pending_approval: dict | None = None
        self._chat_in_flight = False
        self._suppress_conversation_selection = False

        self.resize(1440, 940)
        self._load_stylesheet()
        self._build_ui()
        self._wire_signals()
        self._apply_language()

        self.refresh_timer = QtCore.QTimer(self)
        self.refresh_timer.timeout.connect(self._poll_runtime)
        self.refresh_timer.start(self.config.ui_poll_interval_ms)

        self.approval_timer = QtCore.QTimer(self)
        self.approval_timer.timeout.connect(self._tick_approval_timer)
        self.approval_timer.start(1000)

        QtCore.QTimer.singleShot(250, self.refresh_all)

    def _t(self, key: str, **kwargs) -> str:
        return translate(
            language=self.current_language,
            system_language=self.system_language,
            key=key,
            **kwargs,
        )

    def _snapshot_from_state(self) -> dict:
        return {
            "provider": self.state.get_provider_settings().model_dump(mode="json"),
            "automation": self.state.get_automation_settings().model_dump(mode="json"),
            "ui": self.state.get_ui_state().model_dump(mode="json"),
            "recent_events": self.state.get_recent_events(),
        }

    def _load_stylesheet(self) -> None:
        self.setStyleSheet(
            """
            QWidget { background: #f5f7fb; color: #172033; font-family: "Segoe UI"; font-size: 12px; }
            QMainWindow { background: #f5f7fb; }
            QGroupBox { border: 1px solid #d8deea; border-radius: 12px; margin-top: 12px; padding-top: 10px; background: #ffffff; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px 0 6px; }
            QLabel[muted="true"] { color: #60708a; font-weight: 400; }
            QPushButton { background: #1d4ed8; color: white; border: none; border-radius: 10px; padding: 8px 14px; font-weight: 600; }
            QPushButton:hover { background: #1b45c2; }
            QPushButton:disabled { background: #97a4be; color: #e7ecf6; }
            QListWidget, QTextBrowser, QTextEdit, QPlainTextEdit, QLineEdit, QComboBox, QSpinBox, QTabWidget::pane { background: #ffffff; border: 1px solid #d8deea; border-radius: 10px; }
            QListWidget::item { padding: 8px; border-radius: 8px; }
            QListWidget::item:selected { background: #dbeafe; color: #102a56; }
            QTabBar::tab { background: #e9eef8; color: #2b3b57; border-top-left-radius: 10px; border-top-right-radius: 10px; padding: 8px 14px; margin-right: 4px; }
            QTabBar::tab:selected { background: #ffffff; color: #0f172a; }
            """
        )

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        header = QtWidgets.QHBoxLayout()
        title_column = QtWidgets.QVBoxLayout()
        self.hero_label = QtWidgets.QLabel()
        hero_font = QtGui.QFont("Segoe UI", 20)
        hero_font.setBold(True)
        self.hero_label.setFont(hero_font)
        self.subtitle_label = QtWidgets.QLabel()
        self.subtitle_label.setProperty("muted", True)
        self.subtitle_label.setWordWrap(True)
        title_column.addWidget(self.hero_label)
        title_column.addWidget(self.subtitle_label)
        header.addLayout(title_column, stretch=1)

        actions = QtWidgets.QHBoxLayout()
        self.language_badge = QtWidgets.QLabel()
        self.language_badge.setProperty("muted", True)
        self.settings_button = QtWidgets.QPushButton()
        self.refresh_button = QtWidgets.QPushButton()
        actions.addWidget(self.language_badge)
        actions.addWidget(self.settings_button)
        actions.addWidget(self.refresh_button)
        header.addLayout(actions)
        root.addLayout(header)

        self.runtime_group = QtWidgets.QGroupBox()
        runtime_layout = QtWidgets.QGridLayout(self.runtime_group)
        self.backend_runtime_label = QtWidgets.QLabel()
        self.provider_runtime_label = QtWidgets.QLabel()
        self.mode_runtime_label = QtWidgets.QLabel()
        self.controller_runtime_label = QtWidgets.QLabel()
        self.backend_status = QtWidgets.QLabel("-")
        self.provider_status = QtWidgets.QLabel("-")
        self.mode_status = QtWidgets.QLabel("-")
        self.controller_status = QtWidgets.QLabel("-")
        runtime_layout.addWidget(self.backend_runtime_label, 0, 0)
        runtime_layout.addWidget(self.backend_status, 0, 1)
        runtime_layout.addWidget(self.provider_runtime_label, 0, 2)
        runtime_layout.addWidget(self.provider_status, 0, 3)
        runtime_layout.addWidget(self.mode_runtime_label, 1, 0)
        runtime_layout.addWidget(self.mode_status, 1, 1)
        runtime_layout.addWidget(self.controller_runtime_label, 1, 2)
        runtime_layout.addWidget(self.controller_status, 1, 3)
        runtime_layout.setColumnStretch(1, 1)
        runtime_layout.setColumnStretch(3, 1)
        root.addWidget(self.runtime_group)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_chat_panel())
        splitter.addWidget(self._build_activity_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, stretch=1)

        self.statusBar().showMessage(self._t("status_ready"))

    def _build_chat_panel(self) -> QtWidgets.QGroupBox:
        self.chat_group = QtWidgets.QGroupBox()
        layout = QtWidgets.QVBoxLayout(self.chat_group)
        layout.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        self.current_conversation_label = QtWidgets.QLabel()
        self.current_conversation_label.setProperty("muted", True)
        self.current_conversation_label.setWordWrap(True)
        self.new_conversation_button = QtWidgets.QPushButton()
        self.reload_conversations_button = QtWidgets.QPushButton()
        header.addWidget(self.current_conversation_label, stretch=1)
        header.addWidget(self.new_conversation_button)
        header.addWidget(self.reload_conversations_button)
        layout.addLayout(header)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)

        self.conversation_list = QtWidgets.QListWidget()
        self.conversation_list.setMinimumWidth(250)
        self.conversation_list.setAlternatingRowColors(True)
        self.conversation_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        splitter.addWidget(self.conversation_list)

        chat_column = QtWidgets.QWidget()
        chat_layout = QtWidgets.QVBoxLayout(chat_column)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(12)

        self.chat_view = QtWidgets.QTextBrowser()
        self.chat_view.setOpenExternalLinks(False)
        chat_layout.addWidget(self.chat_view, stretch=1)

        self.prompt_group = QtWidgets.QGroupBox()
        prompt_layout = QtWidgets.QVBoxLayout(self.prompt_group)
        prompt_layout.setSpacing(10)
        attachment_row = QtWidgets.QHBoxLayout()
        self.chat_attachment_label = QtWidgets.QLabel()
        self.chat_attachment_label.setProperty("muted", True)
        self.attach_images_button = QtWidgets.QPushButton()
        self.clear_images_button = QtWidgets.QPushButton()
        attachment_row.addWidget(self.chat_attachment_label, stretch=1)
        attachment_row.addWidget(self.attach_images_button)
        attachment_row.addWidget(self.clear_images_button)
        prompt_layout.addLayout(attachment_row)
        self.prompt_input = QtWidgets.QPlainTextEdit()
        self.prompt_input.setFixedHeight(140)
        prompt_layout.addWidget(self.prompt_input)
        send_row = QtWidgets.QHBoxLayout()
        send_row.addStretch(1)
        self.send_button = QtWidgets.QPushButton()
        send_row.addWidget(self.send_button)
        prompt_layout.addLayout(send_row)
        chat_layout.addWidget(self.prompt_group)

        splitter.addWidget(chat_column)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter)
        return self.chat_group

    def _build_activity_panel(self) -> QtWidgets.QGroupBox:
        self.activity_group = QtWidgets.QGroupBox()
        layout = QtWidgets.QVBoxLayout(self.activity_group)
        layout.setSpacing(12)

        self.activity_hint_label = QtWidgets.QLabel()
        self.activity_hint_label.setProperty("muted", True)
        self.activity_hint_label.setWordWrap(True)
        layout.addWidget(self.activity_hint_label)

        self.task_tabs = QtWidgets.QTabWidget()
        self.task_tabs.setDocumentMode(True)
        layout.addWidget(self.task_tabs, stretch=1)

        self.approval_group = QtWidgets.QGroupBox()
        approval_layout = QtWidgets.QVBoxLayout(self.approval_group)
        approval_layout.setSpacing(8)
        self.approval_summary_label = QtWidgets.QLabel()
        self.approval_summary_label.setWordWrap(True)
        self.approval_countdown_label = QtWidgets.QLabel()
        self.approval_countdown_label.setProperty("muted", True)
        self.approval_details_view = QtWidgets.QTextBrowser()
        self.approval_details_view.setOpenExternalLinks(True)
        self.approval_details_view.setMinimumHeight(130)
        self.approval_prompt_input = QtWidgets.QPlainTextEdit()
        self.approval_prompt_input.setFixedHeight(80)
        self.approval_allow_button = QtWidgets.QPushButton()
        self.approval_deny_button = QtWidgets.QPushButton()
        self.approval_prompt_button = QtWidgets.QPushButton()
        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.approval_allow_button)
        button_row.addWidget(self.approval_deny_button)
        button_row.addWidget(self.approval_prompt_button)
        approval_layout.addWidget(self.approval_summary_label)
        approval_layout.addWidget(self.approval_countdown_label)
        approval_layout.addWidget(self.approval_details_view)
        approval_layout.addWidget(self.approval_prompt_input)
        approval_layout.addLayout(button_row)
        layout.addWidget(self.approval_group)

        self.events_group = QtWidgets.QGroupBox()
        events_layout = QtWidgets.QVBoxLayout(self.events_group)
        self.events_view = QtWidgets.QTextBrowser()
        self.events_view.setOpenExternalLinks(False)
        self.events_view.setMinimumHeight(180)
        events_layout.addWidget(self.events_view)
        layout.addWidget(self.events_group)
        return self.activity_group

    def _wire_signals(self) -> None:
        self.chat_view.anchorClicked.connect(self._on_browser_link_clicked)
        self.settings_button.clicked.connect(self.open_settings_dialog)
        self.refresh_button.clicked.connect(self.refresh_all)
        self.new_conversation_button.clicked.connect(self.create_conversation)
        self.reload_conversations_button.clicked.connect(self.refresh_all)
        self.conversation_list.currentItemChanged.connect(self._on_conversation_selected)
        self.attach_images_button.clicked.connect(self.choose_chat_attachments)
        self.clear_images_button.clicked.connect(self.clear_chat_attachments)
        self.send_button.clicked.connect(self.send_chat_message)
        self.task_tabs.currentChanged.connect(self._on_task_tab_changed)
        self.approval_allow_button.clicked.connect(
            lambda: self._submit_selected_task_approval(ApprovalTimeoutAction.ALLOW.value)
        )
        self.approval_deny_button.clicked.connect(
            lambda: self._submit_selected_task_approval(ApprovalTimeoutAction.DENY.value)
        )
        self.approval_prompt_button.clicked.connect(
            lambda: self._submit_selected_task_approval(ApprovalTimeoutAction.PROMPT.value)
        )
        self.send_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self)
        self.send_shortcut.activated.connect(self.send_chat_message)

    def _apply_language(self) -> None:
        self.setWindowTitle(self._t("app_title"))
        self.hero_label.setText(self._t("app_title"))
        self.subtitle_label.setText(self._t("subtitle"))
        self.language_badge.setText(
            f"{self._t('language')}: "
            f"{'System' if self.current_language == SYSTEM_LANGUAGE else self.current_language}"
        )
        self.settings_button.setText(self._t("settings"))
        self.refresh_button.setText(self._t("refresh"))
        self.runtime_group.setTitle(self._t("runtime"))
        self.backend_runtime_label.setText(self._t("backend"))
        self.provider_runtime_label.setText(self._t("provider"))
        self.mode_runtime_label.setText(self._t("control_mode"))
        self.controller_runtime_label.setText(self._t("controller"))
        self.chat_group.setTitle(self._t("conversation"))
        self.new_conversation_button.setText(self._t("new_conversation"))
        self.reload_conversations_button.setText(self._t("reload_history"))
        self.prompt_group.setTitle(self._t("prompt"))
        self.attach_images_button.setText(self._t("attach_images"))
        self.clear_images_button.setText(self._t("clear_images"))
        self.prompt_input.setPlaceholderText(self._t("prompt_placeholder"))
        self.send_button.setText(self._t("send"))
        self.activity_group.setTitle(self._t("activity"))
        self.activity_hint_label.setText(self._t("activity_hint"))
        self.approval_group.setTitle(self._t("task_pending_approval"))
        self.approval_prompt_input.setPlaceholderText(
            self._t("task_approval_prompt_placeholder")
        )
        self.approval_allow_button.setText(self._t("approval_allow"))
        self.approval_deny_button.setText(self._t("approval_deny"))
        self.approval_prompt_button.setText(self._t("approval_prompt"))
        self.events_group.setTitle(self._t("events"))
        self._update_chat_attachment_status()
        self._update_conversation_banner()
        self._render_events(self._last_settings_payload.get("recent_events", []))
        self._rebuild_task_tabs(list(self._task_payloads.values()), self.selected_task_id)
        if self._loaded_conversation_payload is not None:
            self._render_chat_history(self._loaded_conversation_payload)
        self._set_pending_approval(self._pending_approval)

    def refresh_all(self) -> None:
        self.refresh_health()
        self.refresh_settings()
        self.refresh_conversations()
        if self.current_conversation_id:
            self.refresh_current_conversation()
        self.statusBar().showMessage(self._t("status_refreshed"), 2500)

    def _poll_runtime(self) -> None:
        self.refresh_health()
        self.refresh_conversations()
        if self.current_conversation_id:
            self.refresh_current_conversation()

    def refresh_health(self) -> None:
        self._queue_request(
            "health",
            "/api/health",
            on_success=self._apply_health,
            error_context="Health check failed",
        )

    def refresh_settings(self) -> None:
        self._queue_request(
            "settings",
            "/api/settings",
            on_success=self._apply_settings_snapshot,
            error_context="Settings refresh failed",
        )

    def refresh_conversations(self) -> None:
        self._queue_request(
            "conversations",
            "/api/conversations",
            on_success=self._apply_conversation_list,
            error_context="Conversation list refresh failed",
        )

    def refresh_current_conversation(self) -> None:
        if not self.current_conversation_id:
            return
        self.load_conversation(self.current_conversation_id)
        self.load_conversation_tasks(self.current_conversation_id)

    def load_conversation(self, conversation_id: str) -> None:
        encoded_id = quote(conversation_id, safe="")
        self._queue_request(
            f"conversation:{conversation_id}",
            f"/api/conversations/{encoded_id}",
            on_success=lambda payload, expected=conversation_id: self._apply_conversation_history(
                payload,
                expected_conversation_id=expected,
            ),
            error_context="Conversation load failed",
        )

    def load_conversation_tasks(self, conversation_id: str) -> None:
        encoded_id = quote(conversation_id, safe="")
        self._queue_request(
            f"tasks:{conversation_id}",
            f"/api/conversations/{encoded_id}/tasks/details",
            on_success=lambda payload, expected=conversation_id: self._apply_task_details(
                payload,
                expected_conversation_id=expected,
            ),
            error_context="Conversation task refresh failed",
        )

    def _queue_request(
        self,
        request_key: str,
        path: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        timeout: float | None = None,
        on_success=None,
        on_failure=None,
        error_context: str = "Request failed",
    ) -> bool:
        worker = self._workers.get(request_key)
        if worker is not None and worker.isRunning():
            return False

        worker = ApiWorker(
            request_key=request_key,
            base_url=self.config.backend_url,
            path=path,
            method=method,
            payload=payload,
            timeout=timeout or self.config.request_timeout_seconds,
        )
        self._workers[request_key] = worker
        worker.succeeded.connect(
            lambda key, response, handler=on_success: self._dispatch_success(key, response, handler)
        )
        worker.failed.connect(
            lambda key, error, handler=on_failure, context=error_context: self._dispatch_failure(
                key,
                error,
                context,
                handler,
            )
        )
        worker.finished.connect(
            lambda key=request_key, current=worker: self._cleanup_worker(key, current)
        )
        worker.start()
        return True

    def _dispatch_success(self, request_key: str, payload: dict, handler) -> None:
        if handler is None:
            return
        try:
            handler(payload)
        except Exception as exc:  # pragma: no cover - UI pathway
            self._show_request_error(f"UI handler failed for {request_key}", str(exc))

    def _dispatch_failure(
        self,
        request_key: str,
        error: str,
        context: str,
        handler,
    ) -> None:
        if handler is not None:
            handler(error)
            return
        self._show_request_error(context, error)
        if request_key == "chat":
            self._set_chat_busy(False)

    def _cleanup_worker(self, request_key: str, worker: ApiWorker) -> None:
        current = self._workers.get(request_key)
        if current is worker:
            self._workers.pop(request_key, None)
        worker.deleteLater()

    def _show_request_error(self, context: str, error: str) -> None:
        self.statusBar().showMessage(f"{context}: {error}", 8000)

    def _apply_health(self, payload: dict) -> None:
        self.backend_status.setText(str(payload.get("status", "unknown")).upper())
        self.provider_status.setText(str(payload.get("provider", "-")))
        self.mode_status.setText(
            self._control_mode_label(str(payload.get("control_mode", ControlMode.ASK.value)))
        )
        self.controller_status.setText(str(payload.get("input_controller", "-")))

    def _apply_settings_snapshot(self, payload: dict) -> None:
        self._last_settings_payload = payload
        automation = payload.get("automation", {})
        ui_state = payload.get("ui", {})

        self.current_language = str(ui_state.get("language", SYSTEM_LANGUAGE))
        self.output_mode = str(ui_state.get("output_mode", OutputMode.FINAL_ONLY.value))
        self.latest_capture_path = ui_state.get("latest_capture_path")
        self.approval_timeout_seconds = int(automation.get("approval_timeout_seconds", 60))
        self.approval_timeout_action = str(
            automation.get("approval_timeout_action", ApprovalTimeoutAction.DENY.value)
        )
        self.approval_timeout_prompt = str(
            automation.get("approval_timeout_prompt", DEFAULT_APPROVAL_TIMEOUT_PROMPT)
        )
        persisted_conversation_id = ui_state.get("current_conversation_id")
        if isinstance(persisted_conversation_id, str) and persisted_conversation_id:
            self.current_conversation_id = persisted_conversation_id

        if self._active_settings_dialog is not None:
            self._active_settings_dialog.set_snapshot(payload)

        self._apply_language()
        if self.current_conversation_id:
            self._select_conversation_in_list(self.current_conversation_id)
            if self.loaded_conversation_id != self.current_conversation_id:
                self.refresh_current_conversation()

    def _apply_conversation_list(self, payload: dict) -> None:
        conversations = payload.get("conversations", [])
        self._suppress_conversation_selection = True
        self.conversation_list.clear()

        for conversation in conversations:
            conversation_id = str(conversation.get("conversation_id", ""))
            title = str(conversation.get("title", conversation_id))
            updated_at = self._compact_timestamp(conversation.get("updated_at"))
            message_count = int(conversation.get("message_count", 0))
            item = QtWidgets.QListWidgetItem(f"{title}\n{updated_at} | {message_count}")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, conversation_id)
            item.setToolTip(conversation_id)
            self.conversation_list.addItem(item)

        target_id = self.current_conversation_id or self.loaded_conversation_id
        selected = self._select_conversation_in_list(target_id) if target_id else False
        if not selected and conversations:
            first_id = str(conversations[0].get("conversation_id", ""))
            selected = self._select_conversation_in_list(first_id)
            if selected:
                self.current_conversation_id = first_id
        self._suppress_conversation_selection = False

        if not conversations:
            self.current_conversation_id = None
            self.loaded_conversation_id = None
            self._loaded_conversation_payload = None
            self._highlighted_message_id = None
            self._pending_task_link_id = None
            self._render_empty_chat()
            self._rebuild_task_tabs([], None)
            self._update_conversation_banner()
            return

        if selected and self.current_conversation_id and self.loaded_conversation_id != self.current_conversation_id:
            self.refresh_current_conversation()
        self._update_conversation_banner()

    def _apply_conversation_history(
        self,
        payload: dict,
        *,
        expected_conversation_id: str,
    ) -> None:
        conversation = payload.get("conversation", {})
        conversation_id = str(conversation.get("conversation_id", ""))
        if conversation_id != expected_conversation_id:
            return
        if self.current_conversation_id and conversation_id != self.current_conversation_id:
            return
        self.current_conversation_id = conversation_id
        self.loaded_conversation_id = conversation_id
        self._loaded_conversation_payload = payload
        self._update_conversation_banner()
        self._render_chat_history(payload)

    def _apply_task_details(
        self,
        payload: dict,
        *,
        expected_conversation_id: str,
    ) -> None:
        if self.current_conversation_id and expected_conversation_id != self.current_conversation_id:
            return
        tasks = payload.get("tasks", [])
        ordered_tasks = [task for task in tasks if isinstance(task, dict) and task.get("task_id")]
        self._task_payloads = {str(task["task_id"]): task for task in ordered_tasks}
        target_id = self.selected_task_id if self.selected_task_id in self._task_payloads else None
        if self._pending_task_link_id:
            pending = self._pending_task_link_id
            self._pending_task_link_id = None
            if pending in self._task_payloads:
                target_id = pending
            else:
                self.statusBar().showMessage(f"未找到对应执行 {pending}", 3500)
        if target_id is None and ordered_tasks and self.selected_task_id is None:
            target_id = str(ordered_tasks[0].get("task_id"))
        self._rebuild_task_tabs(ordered_tasks, target_id)

    def _rebuild_task_tabs(self, tasks: list[dict], target_task_id: str | None) -> None:
        self.task_tabs.blockSignals(True)
        while self.task_tabs.count():
            widget = self.task_tabs.widget(0)
            self.task_tabs.removeTab(0)
            if widget is not None:
                widget.deleteLater()
        self._task_pages.clear()

        if not tasks:
            placeholder = QtWidgets.QTextBrowser()
            placeholder.setHtml(self._wrap_html_body(f"<p>{escape(self._t('activity_empty'))}</p>"))
            self.task_tabs.addTab(placeholder, self._t("activity"))
            self.selected_task_id = None
            self._set_pending_approval(None)
            self.task_tabs.blockSignals(False)
            return

        selected_index = 0
        for index, task in enumerate(tasks):
            refs = self._build_task_page(task)
            task_id = str(task.get("task_id", ""))
            refs.widget.setProperty("task_id", task_id)
            self._task_pages[task_id] = refs
            self.task_tabs.addTab(refs.widget, self._task_tab_label(task))
            if task_id == target_task_id:
                selected_index = index

        self.task_tabs.setCurrentIndex(selected_index)
        self.task_tabs.blockSignals(False)
        self._on_task_tab_changed(selected_index)

    def _build_task_page(self, task: dict) -> TaskPageRefs:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        summary_view = QtWidgets.QTextBrowser()
        summary_view.setOpenExternalLinks(False)
        summary_view.anchorClicked.connect(self._on_browser_link_clicked)
        summary_view.setMinimumHeight(220)
        layout.addWidget(summary_view)

        agent_tabs = QtWidgets.QTabWidget()
        agent_tabs.setDocumentMode(True)
        layout.addWidget(agent_tabs, stretch=1)

        refs = TaskPageRefs(widget=widget, summary_view=summary_view, agent_tabs=agent_tabs)
        self._populate_task_page(refs, task)
        return refs

    def _populate_task_page(self, refs: TaskPageRefs, task: dict) -> None:
        refs.summary_view.setHtml(self._render_task_summary(task))
        while refs.agent_tabs.count():
            widget = refs.agent_tabs.widget(0)
            refs.agent_tabs.removeTab(0)
            if widget is not None:
                widget.deleteLater()

        agents = task.get("agents", [])
        if not agents:
            placeholder = QtWidgets.QTextBrowser()
            placeholder.setHtml(self._wrap_html_body(f"<p>{escape(self._t('task_no_agents'))}</p>"))
            refs.agent_tabs.addTab(placeholder, self._t("task_agents"))
            return

        for agent in agents:
            browser = QtWidgets.QTextBrowser()
            browser.setOpenExternalLinks(True)
            browser.setHtml(self._render_agent_html(agent, agents))
            refs.agent_tabs.addTab(browser, self._agent_tab_label(agent))

    def _render_task_summary(self, task: dict) -> str:
        title = escape(str(task.get("title", self._t("activity"))).strip() or self._t("activity"))
        status = escape(self._task_status_label(str(task.get("status", "draft"))))
        last_message = escape(str(task.get("last_message") or self._t("label_none")))
        source_mapping_html = self._render_task_source_mapping(task)
        agents = task.get("agents", [])
        results = task.get("results", [])
        html: list[str] = [
            f"<h3>{title}</h3>",
            "<table>",
            f"<tr><td><b>{escape(self._t('task_status'))}</b></td><td>{status}</td></tr>",
            f"<tr><td><b>{escape(self._t('task_agents'))}</b></td><td>{len(agents)}</td></tr>",
            f"<tr><td><b>{escape(self._t('task_last_message'))}</b></td><td>{last_message}</td></tr>",
            f"<tr><td><b>触发消息</b></td><td>{source_mapping_html}</td></tr>",
            "</table>",
        ]

        pending = task.get("pending_approval") or {}
        if pending:
            summary = escape(str(pending.get("summary") or self._t("approval_none")))
            html.extend([f"<h4>{escape(self._t('task_pending_approval'))}</h4>", f"<p>{summary}</p>"])

        if results:
            html.append(f"<h4>{escape(self._t('task_results'))}</h4>")
            html.append("<ol>")
            subset = results if self.output_mode == OutputMode.STEP_SUMMARY.value else results[-3:]
            for result in subset:
                html.append(self._render_result_item(result))
            html.append("</ol>")
            if self.output_mode != OutputMode.STEP_SUMMARY.value and len(results) > 3:
                html.append(f"<p><i>+ {len(results) - 3} earlier entries</i></p>")

        return self._wrap_html_body("".join(html))

    def _render_result_item(self, result: dict) -> str:
        kind = escape(str(result.get("kind", "step")).replace("_", " ").title())
        message = escape(str(result.get("message", "")))
        label = str(result.get("label") or "").strip()
        heading = kind if not label else f"{kind} | {escape(label)}"
        output_html = ""
        output = result.get("output")
        if self.output_mode == OutputMode.STEP_SUMMARY.value and isinstance(output, dict) and output:
            lines = [
                f"<li><b>{escape(str(key))}</b>: {escape(str(value))}</li>"
                for key, value in output.items()
            ]
            output_html = f"<ul>{''.join(lines)}</ul>"
        return f"<li><b>{heading}</b><br>{message}{output_html}</li>"

    def _render_agent_html(self, agent: dict, all_agents: list[dict]) -> str:
        name = escape(str(agent.get("name", "Agent")))
        status = escape(self._task_status_label(str(agent.get("status", "draft"))))
        instruction = escape(str(agent.get("instruction") or self._t("label_none")))
        autonomous = bool(agent.get("autonomous", False))
        max_iterations = int(agent.get("max_iterations", 0) or 0)
        mode_label = (
            self._t("agent_mode_autonomous")
            if autonomous
            else self._t("agent_mode_seeded")
        )
        assignment = agent.get("model_assignment") or {}
        provider = assignment.get("provider") or self._t("label_inherit")
        model = assignment.get("model") or self._t("label_inherit")
        base_url = assignment.get("base_url") or self._t("label_inherit")
        reason = assignment.get("assignment_reason") or self._t("label_none")
        child_count = sum(
            1
            for item in all_agents
            if str(item.get("parent_agent_id") or "") == str(agent.get("agent_id", ""))
        )
        results = agent.get("results", [])

        html: list[str] = [
            f"<h3>{name}</h3>",
            "<table>",
            f"<tr><td><b>{escape(self._t('agent_status'))}</b></td><td>{status}</td></tr>",
            f"<tr><td><b>{escape(self._t('agent_instruction'))}</b></td><td>{instruction}</td></tr>",
            f"<tr><td><b>{escape(self._t('agent_mode'))}</b></td><td>{escape(mode_label)}</td></tr>",
            f"<tr><td><b>{escape(self._t('agent_turns'))}</b></td><td>{max_iterations}</td></tr>",
            f"<tr><td><b>{escape(self._t('agent_model'))}</b></td><td>{escape(str(provider))} / {escape(str(model))}<br>{escape(str(base_url))}</td></tr>",
            f"<tr><td><b>{escape(self._t('agent_assignment_reason'))}</b></td><td>{escape(str(reason))}</td></tr>",
            f"<tr><td><b>{escape(self._t('agent_children'))}</b></td><td>{child_count}</td></tr>",
            "</table>",
        ]

        if results:
            html.append(f"<h4>{escape(self._t('task_results'))}</h4>")
            html.append("<ol>")
            subset = results if self.output_mode == OutputMode.STEP_SUMMARY.value else results[-4:]
            for result in subset:
                html.append(self._render_result_item(result))
            html.append("</ol>")
        return self._wrap_html_body("".join(html))

    def _render_chat_history(self, payload: dict) -> None:
        conversation = payload.get("conversation", {})
        messages = payload.get("messages", [])
        title = escape(str(conversation.get("title", self._t("conversation"))))
        blocks: list[str] = [f"<h3>{title}</h3>"]
        for message in messages:
            role = str(message.get("role", "assistant"))
            role_label = self._t("chat_role_user" if role == "user" else "chat_role_assistant")
            timestamp = self._compact_timestamp(message.get("created_at"))
            content = escape(str(message.get("content", ""))).replace("\n", "<br>")
            message_id = str(message.get("message_id") or "").strip()
            linked_task_id = str(message.get("linked_task_id") or "").strip()
            is_highlighted = bool(
                message_id and message_id == (self._highlighted_message_id or "")
            )
            border_color = "#2563eb" if is_highlighted else "#d8deea"
            background = "#eff6ff" if is_highlighted else "#ffffff"
            attachments = self._render_message_attachments(message.get("attachments", []))
            mapping = self._render_message_task_mapping(
                message_id=message_id,
                linked_task_id=linked_task_id,
            )
            anchor_html = (
                f"<a id='{escape(message_anchor_name(message_id))}'></a>" if message_id else ""
            )
            blocks.append(
                (
                    "<div style='margin-bottom:16px;padding:14px;"
                    f"border:1px solid {border_color};border-radius:12px;background:{background};'>"
                    f"{anchor_html}"
                    f"<div style='font-weight:700;margin-bottom:6px;'>{escape(role_label)}</div>"
                    f"<div style='color:#60708a;margin-bottom:8px;'>{escape(timestamp)}</div>"
                    f"<div>{content or '&nbsp;'}</div>{mapping}{attachments}</div>"
                )
            )
        if not messages:
            blocks.append(f"<p>{escape(self._t('conversation_empty'))}</p>")
        self.chat_view.setHtml(self._wrap_html_body("".join(blocks)))
        if self._highlighted_message_id:
            self.chat_view.scrollToAnchor(message_anchor_name(self._highlighted_message_id))

    def _render_message_task_mapping(self, *, message_id: str, linked_task_id: str) -> str:
        if linked_task_id:
            task_link = build_internal_link(TASK_LINK_SCHEME, linked_task_id)
            return (
                "<div style='margin-top:10px;color:#1e3a8a;'>"
                f"<b>对应执行</b>: <a href='{escape(task_link)}'>{escape(linked_task_id)}</a>"
                "</div>"
            )
        if not message_id:
            return ""
        return "<div style='margin-top:10px;color:#60708a;'><b>对应执行</b>: 无映射</div>"

    def _render_task_source_mapping(self, task: dict) -> str:
        source_message_id = str(task.get("source_message_id") or "").strip()
        if not source_message_id:
            return "无映射"
        message_link = build_internal_link(MESSAGE_LINK_SCHEME, source_message_id)
        preview = str(task.get("source_message_preview") or "").strip()
        preview_html = (
            f"<div style='color:#60708a;margin-top:4px;'>{escape(preview[:72])}</div>"
            if preview
            else ""
        )
        return (
            f"<a href='{escape(message_link)}'>{escape(source_message_id)}</a>"
            f"{preview_html}"
        )

    def _render_message_attachments(self, attachments: list[dict]) -> str:
        if not attachments:
            return ""
        parts: list[str] = ["<div style='margin-top:10px;'>"]
        for attachment in attachments:
            name = str(attachment.get("name") or "image")
            image_path = attachment.get("image_path")
            if isinstance(image_path, str) and image_path:
                try:
                    uri = Path(image_path).resolve().as_uri()
                except ValueError:
                    uri = ""
                if uri:
                    parts.append(
                        (
                            f"<div style='margin-top:8px;'><a href='{uri}'>{escape(name)}</a><br>"
                            f"<img src='{uri}' style='max-width:240px;max-height:180px;margin-top:6px;"
                            "border:1px solid #d8deea;border-radius:10px;'></div>"
                        )
                    )
                    continue
            parts.append(f"<div style='margin-top:8px;'>{escape(name)}</div>")
        parts.append("</div>")
        return "".join(parts)

    def _render_events(self, events: list[str]) -> None:
        if not events:
            self.events_view.setHtml(self._wrap_html_body(f"<p>{escape(self._t('events_placeholder'))}</p>"))
            return
        lines = "<br>".join(escape(item) for item in events)
        self.events_view.setHtml(self._wrap_html_body(f"<p>{lines}</p>"))

    def _render_empty_chat(self) -> None:
        self.chat_view.setHtml(self._wrap_html_body(f"<p>{escape(self._t('conversation_empty'))}</p>"))

    def _update_conversation_banner(self) -> None:
        if self._loaded_conversation_payload is not None:
            conversation = self._loaded_conversation_payload.get("conversation", {})
            title = str(conversation.get("title", self._t("conversation_none")))
            count = int(conversation.get("message_count", 0))
            self.current_conversation_label.setText(f"{title} | {count}")
            return
        if self.current_conversation_id:
            self.current_conversation_label.setText(self.current_conversation_id)
            return
        self.current_conversation_label.setText(self._t("conversation_none"))

    def choose_chat_attachments(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            self._t("attach_images"),
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp *.gif)",
        )
        if not paths:
            return
        self._selected_chat_attachments = [str(Path(path)) for path in paths]
        self._update_chat_attachment_status()
        self.statusBar().showMessage(
            self._t("status_images_selected", count=len(self._selected_chat_attachments)),
            3000,
        )

    def clear_chat_attachments(self) -> None:
        self._selected_chat_attachments.clear()
        self._update_chat_attachment_status()
        self.statusBar().showMessage(self._t("status_images_cleared"), 2500)

    def _update_chat_attachment_status(self) -> None:
        if not self._selected_chat_attachments:
            self.chat_attachment_label.setText(self._t("chat_attachments_none"))
        else:
            names = ", ".join(Path(path).name for path in self._selected_chat_attachments)
            self.chat_attachment_label.setText(
                self._t(
                    "chat_attachments_ready",
                    count=len(self._selected_chat_attachments),
                    names=names,
                )
            )
        self.clear_images_button.setEnabled(bool(self._selected_chat_attachments) and not self._chat_in_flight)

    def send_chat_message(self) -> None:
        if self._chat_in_flight:
            return
        message = self.prompt_input.toPlainText().strip()
        if not message and not self._selected_chat_attachments:
            self.statusBar().showMessage(self._t("status_enter_message"), 3000)
            return

        payload = {
            "message": message,
            "conversation_id": self.current_conversation_id,
            "attachments": self._chat_attachment_payloads(),
        }
        self._set_chat_busy(True)
        self.statusBar().showMessage(self._t("status_message_sending"), 3000)
        self._queue_request(
            "chat",
            "/api/chat",
            method="POST",
            payload=payload,
            timeout=max(self.config.request_timeout_seconds, 120.0),
            on_success=self._on_chat_response,
            on_failure=self._on_chat_failure,
            error_context="Chat request failed",
        )

    def _chat_attachment_payloads(self) -> list[dict]:
        payloads: list[dict] = []
        for path in self._selected_chat_attachments:
            media_type = mimetypes.guess_type(path)[0] or "image/png"
            payloads.append(
                {"name": Path(path).name, "media_type": media_type, "image_path": path}
            )
        return payloads

    def _set_chat_busy(self, busy: bool) -> None:
        self._chat_in_flight = busy
        self.prompt_input.setEnabled(not busy)
        self.attach_images_button.setEnabled(not busy)
        self.clear_images_button.setEnabled(not busy and bool(self._selected_chat_attachments))
        self.send_button.setEnabled(not busy)

    def _on_chat_response(self, payload: dict) -> None:
        self._set_chat_busy(False)
        self.prompt_input.clear()
        self._selected_chat_attachments.clear()
        self._update_chat_attachment_status()

        conversation_id = payload.get("conversation_id")
        if isinstance(conversation_id, str) and conversation_id:
            self.current_conversation_id = conversation_id
            self.loaded_conversation_id = conversation_id
        task_id = payload.get("task_id")
        if isinstance(task_id, str) and task_id:
            self.selected_task_id = task_id

        self.refresh_settings()
        self.refresh_conversations()
        if self.current_conversation_id:
            self.refresh_current_conversation()

        task_status = str(payload.get("task_status") or "").strip()
        if task_status == "completed":
            message = self._t("status_chat_task_completed")
        elif task_status == "waiting_approval":
            message = self._t("status_chat_task_waiting")
        elif task_status == "failed":
            message = self._t("status_chat_task_failed")
        else:
            message = self._t("status_chat_task_running")
        self.statusBar().showMessage(message, 5000)

    def _on_chat_failure(self, error: str) -> None:
        self._set_chat_busy(False)
        self._show_request_error("Chat request failed", error)

    def create_conversation(self) -> None:
        self._queue_request(
            "conversation:create",
            "/api/conversations",
            method="POST",
            payload={},
            on_success=self._on_conversation_created,
            error_context="Conversation creation failed",
        )

    def _on_conversation_created(self, payload: dict) -> None:
        conversation = payload.get("conversation", {})
        conversation_id = str(conversation.get("conversation_id", ""))
        if conversation_id:
            self.current_conversation_id = conversation_id
            self.loaded_conversation_id = conversation_id
        self._loaded_conversation_payload = payload
        self._render_chat_history(payload)
        self._update_conversation_banner()
        self.refresh_conversations()
        self.refresh_settings()
        self.load_conversation_tasks(conversation_id)
        self.statusBar().showMessage(self._t("status_conversation_created"), 3000)

    def _on_conversation_selected(
        self,
        current: QtWidgets.QListWidgetItem | None,
        previous: QtWidgets.QListWidgetItem | None,
    ) -> None:
        del previous
        if self._suppress_conversation_selection or current is None:
            return
        conversation_id = current.data(QtCore.Qt.ItemDataRole.UserRole)
        if not isinstance(conversation_id, str) or not conversation_id:
            return
        if conversation_id == self.current_conversation_id and conversation_id == self.loaded_conversation_id:
            return
        self.current_conversation_id = conversation_id
        self.selected_task_id = None
        self._highlighted_message_id = None
        self._pending_task_link_id = None
        self._queue_request(
            "ui:conversation",
            "/api/settings/ui",
            method="POST",
            payload={"current_conversation_id": conversation_id},
            error_context="Conversation state persistence failed",
        )
        self.refresh_current_conversation()

    def _select_conversation_in_list(self, conversation_id: str | None) -> bool:
        if not conversation_id:
            return False
        for index in range(self.conversation_list.count()):
            item = self.conversation_list.item(index)
            if item.data(QtCore.Qt.ItemDataRole.UserRole) == conversation_id:
                self.conversation_list.setCurrentItem(item)
                return True
        return False

    def _on_task_tab_changed(self, index: int) -> None:
        widget = self.task_tabs.widget(index)
        task_id = widget.property("task_id") if widget is not None else None
        if not isinstance(task_id, str) or not task_id:
            self.selected_task_id = None
            self._set_highlighted_message(None)
            self._set_pending_approval(None)
            return
        self.selected_task_id = task_id
        task = self._task_payloads.get(task_id)
        source_message_id = (
            str(task.get("source_message_id") or "").strip() if isinstance(task, dict) else ""
        )
        self._set_highlighted_message(source_message_id or None)
        self._set_pending_approval(task.get("pending_approval") if task else None)

    def _on_browser_link_clicked(self, url: QtCore.QUrl) -> None:
        parsed = parse_internal_link(url.toString())
        if parsed is None:
            QtGui.QDesktopServices.openUrl(url)
            return
        scheme, identifier = parsed
        if scheme == TASK_LINK_SCHEME:
            self._focus_task_from_link(identifier)
            return
        if scheme == MESSAGE_LINK_SCHEME:
            self._focus_message_from_link(identifier)
            return
        QtGui.QDesktopServices.openUrl(url)

    def _focus_task_from_link(self, task_id: str) -> None:
        normalized = task_id.strip()
        if not normalized:
            return
        for index in range(self.task_tabs.count()):
            widget = self.task_tabs.widget(index)
            widget_task_id = widget.property("task_id") if widget is not None else None
            if widget_task_id == normalized:
                self.task_tabs.setCurrentIndex(index)
                self.statusBar().showMessage(f"已定位到对应执行 {normalized}", 3000)
                return
        self._pending_task_link_id = normalized
        if self.current_conversation_id:
            self.load_conversation_tasks(self.current_conversation_id)
        self.statusBar().showMessage(f"正在加载执行 {normalized}", 3000)

    def _focus_message_from_link(self, message_id: str) -> None:
        normalized = message_id.strip()
        if not normalized:
            return
        self._set_highlighted_message(normalized)
        self.statusBar().showMessage(f"已定位到触发消息 {normalized}", 3000)

    def _set_highlighted_message(self, message_id: str | None) -> None:
        normalized = (message_id or "").strip() or None
        if normalized == self._highlighted_message_id and self._loaded_conversation_payload is not None:
            if normalized:
                self.chat_view.scrollToAnchor(message_anchor_name(normalized))
            return
        self._highlighted_message_id = normalized
        if self._loaded_conversation_payload is not None:
            self._render_chat_history(self._loaded_conversation_payload)

    def _set_pending_approval(self, pending: dict | None) -> None:
        previous_id = (
            str(self._pending_approval.get("confirmation_id", ""))
            if isinstance(self._pending_approval, dict)
            else ""
        )
        current_id = str(pending.get("confirmation_id", "")) if isinstance(pending, dict) else ""
        self._pending_approval = pending if isinstance(pending, dict) else None
        if previous_id != current_id:
            self.approval_prompt_input.clear()

        visible = self._pending_approval is not None
        self.approval_group.setVisible(visible)
        if not visible:
            self.approval_summary_label.setText(self._t("approval_none"))
            self.approval_details_view.setHtml(self._wrap_html_body(f"<p>{escape(self._t('approval_none'))}</p>"))
            self.approval_countdown_label.clear()
            return

        summary = str(self._pending_approval.get("summary") or self._t("approval_none"))
        self.approval_summary_label.setText(summary)
        self.approval_details_view.setHtml(self._render_pending_approval(self._pending_approval))
        self._refresh_pending_approval_labels()

    def _render_pending_approval(self, pending: dict) -> str:
        risk = escape(str(pending.get("risk_level", "high")))
        preview = escape(str(pending.get("preview") or self._t("label_none"))).replace("\n", "<br>")
        warnings = pending.get("warnings") or []
        warning_html = "".join(f"<li>{escape(str(item))}</li>" for item in warnings)
        body = [f"<p><b>{escape(self._t('task_risk'))}</b>: {risk}</p>"]
        if warning_html:
            body.append(f"<h4>{escape(self._t('task_pending_details'))}</h4><ul>{warning_html}</ul>")
        body.append(f"<p><b>Preview</b></p><p>{preview}</p>")
        return self._wrap_html_body("".join(body))

    def _refresh_pending_approval_labels(self) -> None:
        if not self._pending_approval:
            return
        remaining = self._approval_remaining_seconds(self._pending_approval)
        timeout_action = str(
            self._pending_approval.get("timeout_action", self.approval_timeout_action)
        )
        self.approval_countdown_label.setText(
            f"{self._t('approval_countdown', seconds=max(0, remaining))} | "
            f"{self._t('approval_timeout_default', action=self._approval_action_label(timeout_action))}"
        )

    def _approval_remaining_seconds(self, pending: dict) -> int:
        expires_at = pending.get("expires_at")
        if not isinstance(expires_at, str) or not expires_at:
            return int(pending.get("approval_timeout_seconds", self.approval_timeout_seconds))
        deadline = QtCore.QDateTime.fromString(expires_at, QtCore.Qt.DateFormat.ISODateWithMs)
        if not deadline.isValid():
            deadline = QtCore.QDateTime.fromString(expires_at, QtCore.Qt.DateFormat.ISODate)
        if not deadline.isValid():
            return int(pending.get("approval_timeout_seconds", self.approval_timeout_seconds))
        deadline = deadline.toUTC()
        now = QtCore.QDateTime.currentDateTimeUtc()
        return max(0, int(now.secsTo(deadline)))

    def _submit_selected_task_approval(self, decision: str) -> None:
        if not self.selected_task_id or not self._pending_approval:
            self.statusBar().showMessage(self._t("status_task_selected_required"), 3000)
            return
        extra_prompt = self.approval_prompt_input.toPlainText().strip() or None
        if decision == ApprovalTimeoutAction.PROMPT.value and not extra_prompt:
            self.statusBar().showMessage(self._t("status_task_prompt_required"), 3000)
            return

        encoded_id = quote(self.selected_task_id, safe="")
        self._queue_request(
            f"approve:{self.selected_task_id}",
            f"/api/tasks/{encoded_id}/approve",
            method="POST",
            payload={"decision": decision, "extra_prompt": extra_prompt},
            on_success=self._on_task_approval_response,
            error_context="Task approval failed",
        )

        status_messages = {
            ApprovalTimeoutAction.ALLOW.value: self._t("status_task_approval_sent"),
            ApprovalTimeoutAction.DENY.value: self._t("status_task_denied"),
            ApprovalTimeoutAction.PROMPT.value: self._t("status_task_prompt_sent"),
        }
        self.statusBar().showMessage(status_messages[decision], 3000)

    def _on_task_approval_response(self, payload: dict) -> None:
        task = payload.get("task", {})
        task_id = str(task.get("task_id", ""))
        if task_id:
            self._task_payloads[task_id] = task
            self.selected_task_id = task_id
        if self.current_conversation_id:
            self.load_conversation_tasks(self.current_conversation_id)
        self.refresh_settings()

    def _tick_approval_timer(self) -> None:
        if not self._pending_approval:
            return
        self._refresh_pending_approval_labels()
        if self._approval_remaining_seconds(self._pending_approval) > 0:
            return
        if self.current_conversation_id:
            self.load_conversation_tasks(self.current_conversation_id)
            self.refresh_settings()

    def open_settings_dialog(self) -> None:
        if self._active_settings_dialog is not None and self._active_settings_dialog.isVisible():
            self._active_settings_dialog.raise_()
            self._active_settings_dialog.activateWindow()
            return

        dialog = SettingsDialog(self._t, self)
        dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.set_snapshot(self._last_settings_payload)
        dialog.accepted.connect(lambda current=dialog: self._save_settings_from_dialog(current))
        dialog.finished.connect(lambda _result, current=dialog: self._close_settings_dialog(current))
        dialog.test_button.clicked.connect(lambda _checked=False, current=dialog: self._test_provider_from_dialog(current))
        dialog.test_all_button.clicked.connect(lambda _checked=False, current=dialog: self._test_all_providers_from_dialog(current))
        dialog.provider_combo.currentIndexChanged.connect(
            lambda _index, current=dialog: self._request_dialog_capabilities(current)
        )
        self._active_settings_dialog = dialog
        self._request_dialog_capabilities(dialog)
        dialog.show()

    def _close_settings_dialog(self, dialog: SettingsDialog) -> None:
        if dialog is self._active_settings_dialog:
            self._active_settings_dialog = None

    def _save_settings_from_dialog(self, dialog: SettingsDialog) -> None:
        self._queue_request(
            "settings:apply",
            "/api/settings/apply",
            method="POST",
            payload=dialog.build_apply_payload(self.config.request_timeout_seconds),
            on_success=self._on_settings_saved,
            error_context="Settings save failed",
        )

    def _on_settings_saved(self, payload: dict) -> None:
        self._apply_settings_snapshot(payload)
        self.refresh_health()
        self.statusBar().showMessage(self._t("status_settings_saved"), 3000)

    def _test_provider_from_dialog(self, dialog: SettingsDialog) -> None:
        dialog.set_health_result(self._t("settings_connectivity_report_loading"))
        self._queue_request(
            "settings:test-provider",
            "/api/provider/health",
            method="POST",
            payload=dialog.build_provider_payload(self.config.request_timeout_seconds),
            on_success=lambda payload, current=dialog: self._apply_dialog_health_result(current, payload),
            error_context="Provider health check failed",
        )

    def _apply_dialog_health_result(self, dialog: SettingsDialog, payload: dict) -> None:
        if dialog is not self._active_settings_dialog:
            return
        state_label = self._t("provider_connected") if payload.get("ok") else self._t("provider_needs_attention")
        dialog.set_health_result(
            f"{state_label} | {payload.get('provider', '-')} / {payload.get('model', '-')}"
        )

    def _test_all_providers_from_dialog(self, dialog: SettingsDialog) -> None:
        dialog.set_connectivity_report(self._t("settings_connectivity_report_loading"))
        self._queue_request(
            "settings:test-all",
            "/api/provider/health/all",
            method="POST",
            payload=dialog.build_provider_payload(self.config.request_timeout_seconds),
            on_success=lambda payload, current=dialog: self._apply_dialog_connectivity_report(current, payload),
            error_context="Provider sweep failed",
        )

    def _apply_dialog_connectivity_report(self, dialog: SettingsDialog, payload: dict) -> None:
        if dialog is not self._active_settings_dialog:
            return
        lines = []
        for result in payload.get("results", []):
            state = self._t("provider_connected") if result.get("ok") else self._t("provider_needs_attention")
            lines.append(
                f"- {result.get('provider', '-')}: {state} | {result.get('model', '-')} | {result.get('message', '')}"
            )
        dialog.set_connectivity_report("\n".join(lines) if lines else self._t("settings_not_tested"))

    def _request_dialog_capabilities(self, dialog: SettingsDialog) -> None:
        dialog.set_capabilities_result(self._t("settings_capabilities_loading"))
        self._queue_request(
            "settings:capabilities",
            "/api/provider/capabilities",
            method="POST",
            payload=dialog.build_provider_payload(self.config.request_timeout_seconds),
            on_success=lambda payload, current=dialog: self._apply_dialog_capabilities(current, payload),
            error_context="Capability summary failed",
        )

    def _apply_dialog_capabilities(self, dialog: SettingsDialog, payload: dict) -> None:
        if dialog is not self._active_settings_dialog:
            return
        lines = []
        for capability in payload.get("capabilities", []):
            vision = self._bool_label(bool(capability.get("supports_vision", False)))
            local = self._bool_label(bool(capability.get("local_runtime", False)))
            lines.append(
                f"- {capability.get('provider', '-')}: model={capability.get('default_model', '-')}, vision={vision}, local={local}"
            )
        dialog.set_capabilities_result("\n".join(lines) if lines else self._t("settings_capabilities_loading"))

    def _task_tab_label(self, task: dict) -> str:
        title = str(task.get("title", self._t("activity"))).strip() or self._t("activity")
        status = self._task_status_label(str(task.get("status", "draft")))
        label = f"{status} · {title}"
        return f"{label[:27]}..." if len(label) > 30 else label

    def _agent_tab_label(self, agent: dict) -> str:
        name = str(agent.get("name", "Agent")).strip() or "Agent"
        return f"{name[:17]}..." if len(name) > 20 else name

    def _task_status_label(self, status: str) -> str:
        mapping = {
            "draft": self._t("task_status_draft"),
            "running": self._t("task_status_running"),
            "waiting_approval": self._t("task_status_waiting_approval"),
            "completed": self._t("task_status_completed"),
            "failed": self._t("task_status_failed"),
        }
        return mapping.get(status, status)

    def _control_mode_label(self, mode: str) -> str:
        mapping = {
            ControlMode.DENY.value: self._t("control_deny"),
            ControlMode.ASK.value: self._t("control_ask"),
            ControlMode.ALLOW_SESSION.value: self._t("control_allow_session"),
            ControlMode.ALLOW_ALWAYS.value: self._t("control_allow_always"),
        }
        return mapping.get(mode, mode)

    def _approval_action_label(self, action: str) -> str:
        mapping = {
            ApprovalTimeoutAction.ALLOW.value: self._t("approval_allow"),
            ApprovalTimeoutAction.DENY.value: self._t("approval_deny"),
            ApprovalTimeoutAction.PROMPT.value: self._t("approval_prompt"),
        }
        return mapping.get(action, action)

    def _bool_label(self, value: bool) -> str:
        return self._t("label_yes") if value else self._t("label_no")

    def _compact_timestamp(self, value) -> str:
        if not isinstance(value, str) or not value:
            return "-"
        dt = QtCore.QDateTime.fromString(value, QtCore.Qt.DateFormat.ISODateWithMs)
        if not dt.isValid():
            dt = QtCore.QDateTime.fromString(value, QtCore.Qt.DateFormat.ISODate)
        return dt.toLocalTime().toString("yyyy-MM-dd HH:mm") if dt.isValid() else value

    @staticmethod
    def _wrap_html_body(content: str) -> str:
        return (
            "<html><body style='font-family:Segoe UI;color:#172033;font-size:12px;line-height:1.55;'>"
            f"{content}</body></html>"
        )
