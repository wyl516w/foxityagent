from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
import mimetypes
from pathlib import Path
from urllib.parse import quote

import httpx
from PySide6 import QtCore, QtGui, QtWidgets

from agent_studio.core.config import AppConfig
from agent_studio.core.models import (
    ApprovalTimeoutAction,
    ControlMode,
    OutputMode,
    ProviderType,
    WorkflowStepType,
)
from agent_studio.core.state import SharedState
from agent_studio.services.backend_server import BackendServer
from agent_studio.ui.i18n import SYSTEM_LANGUAGE, translate
from agent_studio.ui.settings_dialog import SettingsDialog


class ApiWorker(QtCore.QThread):
    succeeded = QtCore.Signal(dict)
    failed = QtCore.Signal(str)

    def __init__(
        self,
        base_url: str,
        path: str,
        method: str = "GET",
        payload: dict | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__()
        self.base_url = base_url
        self.path = path
        self.method = method
        self.payload = payload
        self.timeout = timeout

    def run(self) -> None:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.request(
                    self.method,
                    f"{self.base_url}{self.path}",
                    json=self.payload,
                )
                response.raise_for_status()
                data = response.json() if response.content else {}
                self.succeeded.emit(data)
        except Exception as exc:  # pragma: no cover - UI pathway
            self.failed.emit(str(exc))


class MainWindow(QtWidgets.QMainWindow):
    def __init__(
        self, config: AppConfig, state: SharedState, backend: BackendServer
    ) -> None:
        super().__init__()
        self.config = config
        self.state = state
        self.backend = backend
        self._workers: list[ApiWorker] = []
        self.current_conversation_id: str | None = None
        self.loaded_conversation_id: str | None = None
        self.latest_capture_path: str | None = None
        self.current_language: str = SYSTEM_LANGUAGE
        self.output_mode: str = OutputMode.FINAL_ONLY.value
        self.system_language = QtCore.QLocale.system().name()
        self._suppress_conversation_selection = False
        self._suppress_task_selection = False
        self._draft_steps: list[dict] = []
        self.selected_task_id: str | None = None
        self._active_settings_dialog: SettingsDialog | None = None
        self._last_task_payload: dict | None = None
        self._last_perception_kind: str | None = None
        self._last_perception_payload: dict | None = None
        self._last_system_info: dict | None = None
        self._pending_script_confirmation_id: str | None = None
        self._selected_chat_attachments: list[str] = []
        self._task_selected_image_path: str | None = None
        self._last_provider_capabilities_payload: dict | None = None
        self._last_provider_connectivity_payload: dict | None = None
        self.approval_timeout_seconds: int = 60
        self.approval_timeout_action: str = ApprovalTimeoutAction.DENY.value
        self.approval_timeout_prompt: str = (
            "Approval timed out. Continue with a safer alternative and avoid the blocked high-risk action."
        )
        self._task_pending_approval: dict | None = None
        self._system_pending_preview: dict | None = None

        self.resize(1400, 920)
        self._load_stylesheet()
        self._build_ui()
        self._wire_signals()
        self._apply_language()

        self.refresh_timer = QtCore.QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_health)
        self.refresh_timer.start(config.ui_poll_interval_ms)

        self.approval_timer = QtCore.QTimer(self)
        self.approval_timer.timeout.connect(self._tick_inline_approval_timers)
        self.approval_timer.start(1000)

        QtCore.QTimer.singleShot(300, self.refresh_settings)

    def _t(self, key: str, **kwargs) -> str:
        return translate(
            language=self.current_language,
            system_language=self.system_language,
            key=key,
            **kwargs,
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
        self.backend_status = QtWidgets.QLabel("Unknown")
        self.provider_status = QtWidgets.QLabel("Unknown")
        self.mode_status = QtWidgets.QLabel("Unknown")
        self.controller_status = QtWidgets.QLabel("Unknown")
        runtime_layout.addWidget(self.backend_runtime_label, 0, 0)
        runtime_layout.addWidget(self.backend_status, 0, 1)
        runtime_layout.addWidget(self.provider_runtime_label, 0, 2)
        runtime_layout.addWidget(self.provider_status, 0, 3)
        runtime_layout.addWidget(self.mode_runtime_label, 1, 0)
        runtime_layout.addWidget(self.mode_status, 1, 1)
        runtime_layout.addWidget(self.controller_runtime_label, 1, 2)
        runtime_layout.addWidget(self.controller_status, 1, 3)
        root.addWidget(self.runtime_group)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_chat_group())
        splitter.addWidget(self._build_workspace_group())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, stretch=1)

        root.addWidget(self._build_prompt_group())
        self.statusBar().showMessage(self._t("status_ready"))

    def _build_chat_group(self) -> QtWidgets.QGroupBox:
        self.chat_group = QtWidgets.QGroupBox()
        layout = QtWidgets.QVBoxLayout(self.chat_group)

        header = QtWidgets.QHBoxLayout()
        self.current_conversation_label = QtWidgets.QLabel()
        self.current_conversation_label.setProperty("muted", True)
        self.new_conversation_button = QtWidgets.QPushButton()
        self.reload_conversations_button = QtWidgets.QPushButton()
        header.addWidget(self.current_conversation_label, stretch=1)
        header.addWidget(self.new_conversation_button)
        header.addWidget(self.reload_conversations_button)
        layout.addLayout(header)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)

        self.conversation_list = QtWidgets.QListWidget()
        self.conversation_list.setMinimumWidth(260)
        self.conversation_list.setAlternatingRowColors(True)
        self.conversation_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        splitter.addWidget(self.conversation_list)

        self.chat_view = QtWidgets.QTextEdit()
        self.chat_view.setReadOnly(True)
        splitter.addWidget(self.chat_view)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        layout.addWidget(splitter)
        return self.chat_group

    def _build_workspace_group(self) -> QtWidgets.QGroupBox:
        self.workspace_group = QtWidgets.QGroupBox()
        layout = QtWidgets.QVBoxLayout(self.workspace_group)
        self.workspace_tabs = QtWidgets.QTabWidget()
        self.workspace_tabs.addTab(self._build_tasks_tab(), "")
        self.workspace_tabs.addTab(self._build_perception_tab(), "")
        self.workspace_tabs.addTab(self._build_system_tab(), "")
        self.workspace_tabs.addTab(self._build_events_tab(), "")
        layout.addWidget(self.workspace_tabs)
        return self.workspace_group

    def _build_tasks_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(tab)

        self.task_saved_group = QtWidgets.QGroupBox()
        saved_layout = QtWidgets.QVBoxLayout(self.task_saved_group)
        self.task_list = QtWidgets.QListWidget()
        saved_layout.addWidget(self.task_list)
        layout.addWidget(self.task_saved_group, stretch=1)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.task_builder_group = QtWidgets.QGroupBox()
        builder_layout = QtWidgets.QGridLayout(self.task_builder_group)
        self.task_title_label = QtWidgets.QLabel()
        self.task_title_input = QtWidgets.QLineEdit()
        self.task_instruction_label = QtWidgets.QLabel()
        self.task_instruction_input = QtWidgets.QPlainTextEdit()
        self.task_instruction_input.setFixedHeight(86)
        self.task_parent_label = QtWidgets.QLabel()
        self.task_parent_combo = QtWidgets.QComboBox()
        self.task_autonomous_checkbox = QtWidgets.QCheckBox()
        self.task_autonomous_checkbox.setChecked(True)
        self.task_max_turns_label = QtWidgets.QLabel()
        self.task_max_turns_spin = QtWidgets.QSpinBox()
        self.task_max_turns_spin.setRange(1, 24)
        self.task_max_turns_spin.setValue(8)
        self.task_step_label = QtWidgets.QLabel()
        self.task_step_combo = QtWidgets.QComboBox()
        self.task_step_value_label = QtWidgets.QLabel()
        self.task_step_value_input = QtWidgets.QLineEdit()
        self.task_step_value_input.setClearButtonEnabled(True)
        self.task_image_label = QtWidgets.QLabel()
        self.task_image_input = QtWidgets.QLineEdit()
        self.task_image_input.setClearButtonEnabled(True)
        self.task_pick_image_button = QtWidgets.QPushButton()
        self.task_use_latest_capture_button = QtWidgets.QPushButton()
        self.task_add_step_button = QtWidgets.QPushButton()
        self.task_clear_steps_button = QtWidgets.QPushButton()
        self.task_add_agent_button = QtWidgets.QPushButton()
        self.task_draft_list = QtWidgets.QListWidget()
        self.task_create_button = QtWidgets.QPushButton()
        self.task_run_button = QtWidgets.QPushButton()
        self.task_approve_button = QtWidgets.QPushButton()
        self.task_deny_button = QtWidgets.QPushButton()
        self.task_prompt_button = QtWidgets.QPushButton()
        self.task_import_suggestions_button = QtWidgets.QPushButton()
        self.task_approve_button.setEnabled(False)
        self.task_deny_button.setEnabled(False)
        self.task_prompt_button.setEnabled(False)
        self.task_import_suggestions_button.setEnabled(False)
        self.task_refresh_button = QtWidgets.QPushButton()
        self.task_approval_group = QtWidgets.QGroupBox()
        task_approval_layout = QtWidgets.QVBoxLayout(self.task_approval_group)
        self.task_approval_summary = QtWidgets.QLabel()
        self.task_approval_summary.setWordWrap(True)
        self.task_approval_timer = QtWidgets.QLabel()
        self.task_approval_timer.setProperty("muted", True)
        self.task_approval_prompt_input = QtWidgets.QPlainTextEdit()
        self.task_approval_prompt_input.setFixedHeight(80)
        task_approval_actions = QtWidgets.QHBoxLayout()
        task_approval_actions.addWidget(self.task_approve_button)
        task_approval_actions.addWidget(self.task_deny_button)
        task_approval_actions.addWidget(self.task_prompt_button)
        task_approval_layout.addWidget(self.task_approval_summary)
        task_approval_layout.addWidget(self.task_approval_timer)
        task_approval_layout.addWidget(self.task_approval_prompt_input)
        task_approval_layout.addLayout(task_approval_actions)
        self.task_approval_group.setVisible(False)

        builder_layout.addWidget(self.task_title_label, 0, 0)
        builder_layout.addWidget(self.task_title_input, 0, 1, 1, 3)
        builder_layout.addWidget(self.task_instruction_label, 1, 0)
        builder_layout.addWidget(self.task_instruction_input, 1, 1, 1, 3)
        builder_layout.addWidget(self.task_parent_label, 2, 0)
        builder_layout.addWidget(self.task_parent_combo, 2, 1, 1, 3)
        builder_layout.addWidget(self.task_autonomous_checkbox, 3, 0, 1, 2)
        builder_layout.addWidget(self.task_max_turns_label, 3, 2)
        builder_layout.addWidget(self.task_max_turns_spin, 3, 3)
        builder_layout.addWidget(self.task_step_label, 4, 0)
        builder_layout.addWidget(self.task_step_combo, 4, 1)
        builder_layout.addWidget(self.task_step_value_label, 4, 2)
        builder_layout.addWidget(self.task_step_value_input, 4, 3)
        builder_layout.addWidget(self.task_image_label, 5, 0)
        builder_layout.addWidget(self.task_image_input, 5, 1)
        builder_layout.addWidget(self.task_pick_image_button, 5, 2)
        builder_layout.addWidget(self.task_use_latest_capture_button, 5, 3)
        builder_layout.addWidget(self.task_add_step_button, 6, 0, 1, 2)
        builder_layout.addWidget(self.task_clear_steps_button, 6, 2, 1, 2)
        builder_layout.addWidget(self.task_draft_list, 7, 0, 1, 4)
        builder_layout.addWidget(self.task_create_button, 8, 0)
        builder_layout.addWidget(self.task_add_agent_button, 8, 1)
        builder_layout.addWidget(self.task_run_button, 8, 2)
        builder_layout.addWidget(self.task_refresh_button, 8, 3)
        builder_layout.addWidget(self.task_import_suggestions_button, 9, 0, 1, 4)
        builder_layout.addWidget(self.task_approval_group, 10, 0, 1, 4)
        right_layout.addWidget(self.task_builder_group)

        self.task_output_group = QtWidgets.QGroupBox()
        task_output_layout = QtWidgets.QVBoxLayout(self.task_output_group)
        self.task_output_tabs = QtWidgets.QTabWidget()
        self.task_output_tabs.setDocumentMode(True)
        self.task_output_tabs.setTabsClosable(True)
        task_output_layout.addWidget(self.task_output_tabs)
        right_layout.addWidget(self.task_output_group, stretch=1)

        layout.addWidget(right, stretch=2)
        return tab

    def _build_perception_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        grid = QtWidgets.QGridLayout()
        self.capture_path_title = QtWidgets.QLabel()
        self.capture_path_label = QtWidgets.QLabel()
        self.capture_path_label.setProperty("muted", True)
        self.capture_button = QtWidgets.QPushButton()
        self.ocr_button = QtWidgets.QPushButton()
        self.find_text_input = QtWidgets.QLineEdit()
        self.find_button = QtWidgets.QPushButton()
        grid.addWidget(self.capture_path_title, 0, 0)
        grid.addWidget(self.capture_path_label, 0, 1)
        grid.addWidget(self.capture_button, 1, 0)
        grid.addWidget(self.ocr_button, 1, 1)
        grid.addWidget(self.find_text_input, 2, 0)
        grid.addWidget(self.find_button, 2, 1)
        layout.addLayout(grid)

        self.perception_output = QtWidgets.QTextEdit()
        self.perception_output.setReadOnly(True)
        layout.addWidget(self.perception_output, stretch=1)
        return tab

    def _build_events_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        self.events_list = QtWidgets.QListWidget()
        layout.addWidget(self.events_list)
        return tab

    def _build_system_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)

        self.system_info_group = QtWidgets.QGroupBox()
        info_layout = QtWidgets.QVBoxLayout(self.system_info_group)
        info_toolbar = QtWidgets.QHBoxLayout()
        self.system_refresh_button = QtWidgets.QPushButton()
        info_toolbar.addStretch(1)
        info_toolbar.addWidget(self.system_refresh_button)
        info_layout.addLayout(info_toolbar)
        self.system_info_output = QtWidgets.QTextEdit()
        self.system_info_output.setReadOnly(True)
        info_layout.addWidget(self.system_info_output)
        layout.addWidget(self.system_info_group, stretch=1)

        self.system_script_group = QtWidgets.QGroupBox()
        script_layout = QtWidgets.QVBoxLayout(self.system_script_group)
        script_toolbar = QtWidgets.QGridLayout()
        self.system_runtime_label = QtWidgets.QLabel()
        self.system_runtime_combo = QtWidgets.QComboBox()
        self.system_runtime_combo.addItem("", "auto")
        self.system_runtime_combo.addItem("", "python")
        self.system_runtime_combo.addItem("", "shell")
        self.system_timeout_label = QtWidgets.QLabel()
        self.system_timeout_spin = QtWidgets.QDoubleSpinBox()
        self.system_timeout_spin.setRange(1.0, 600.0)
        self.system_timeout_spin.setDecimals(1)
        self.system_timeout_spin.setValue(30.0)
        self.system_run_script_button = QtWidgets.QPushButton()
        script_toolbar.addWidget(self.system_runtime_label, 0, 0)
        script_toolbar.addWidget(self.system_runtime_combo, 0, 1)
        script_toolbar.addWidget(self.system_timeout_label, 0, 2)
        script_toolbar.addWidget(self.system_timeout_spin, 0, 3)
        script_toolbar.addWidget(self.system_run_script_button, 0, 4)
        script_layout.addLayout(script_toolbar)
        self.system_script_input = QtWidgets.QPlainTextEdit()
        self.system_script_input.setFixedHeight(120)
        script_layout.addWidget(self.system_script_input)
        self.system_approval_group = QtWidgets.QGroupBox()
        self.system_approval_group.setVisible(False)
        system_approval_layout = QtWidgets.QVBoxLayout(self.system_approval_group)
        self.system_approval_summary = QtWidgets.QLabel()
        self.system_approval_summary.setWordWrap(True)
        self.system_approval_timer = QtWidgets.QLabel()
        self.system_approval_timer.setProperty("muted", True)
        self.system_prompt_input = QtWidgets.QPlainTextEdit()
        self.system_prompt_input.setFixedHeight(80)
        system_approval_actions = QtWidgets.QHBoxLayout()
        self.system_approve_button = QtWidgets.QPushButton()
        self.system_deny_button = QtWidgets.QPushButton()
        self.system_prompt_button = QtWidgets.QPushButton()
        system_approval_actions.addWidget(self.system_approve_button)
        system_approval_actions.addWidget(self.system_deny_button)
        system_approval_actions.addWidget(self.system_prompt_button)
        system_approval_layout.addWidget(self.system_approval_summary)
        system_approval_layout.addWidget(self.system_approval_timer)
        system_approval_layout.addWidget(self.system_prompt_input)
        system_approval_layout.addLayout(system_approval_actions)
        script_layout.addWidget(self.system_approval_group)
        self.system_script_output = QtWidgets.QTextEdit()
        self.system_script_output.setReadOnly(True)
        script_layout.addWidget(self.system_script_output)
        layout.addWidget(self.system_script_group, stretch=2)
        return tab

    def _build_prompt_group(self) -> QtWidgets.QGroupBox:
        self.prompt_group = QtWidgets.QGroupBox()
        layout = QtWidgets.QVBoxLayout(self.prompt_group)
        attachment_bar = QtWidgets.QHBoxLayout()
        self.chat_attachment_label = QtWidgets.QLabel()
        self.chat_attachment_label.setProperty("muted", True)
        self.chat_attachment_label.setWordWrap(True)
        self.attach_images_button = QtWidgets.QPushButton()
        self.clear_images_button = QtWidgets.QPushButton()
        attachment_bar.addWidget(self.chat_attachment_label, stretch=1)
        attachment_bar.addWidget(self.attach_images_button)
        attachment_bar.addWidget(self.clear_images_button)
        layout.addLayout(attachment_bar)
        self.prompt_input = QtWidgets.QPlainTextEdit()
        self.prompt_input.setFixedHeight(150)
        self.send_button = QtWidgets.QPushButton()
        self.start_local_task_button = QtWidgets.QPushButton()
        prompt_actions = QtWidgets.QHBoxLayout()
        prompt_actions.addStretch(1)
        prompt_actions.addWidget(self.start_local_task_button)
        prompt_actions.addWidget(self.send_button)
        layout.addWidget(self.prompt_input)
        layout.addLayout(prompt_actions)
        return self.prompt_group

    def _wire_signals(self) -> None:
        self.settings_button.clicked.connect(self.open_settings_dialog)
        self.refresh_button.clicked.connect(self.refresh_settings)
        self.capture_button.clicked.connect(self.capture_screen)
        self.ocr_button.clicked.connect(self.run_ocr)
        self.find_button.clicked.connect(self.find_text)
        self.system_refresh_button.clicked.connect(self.refresh_system_info)
        self.system_run_script_button.clicked.connect(self.review_and_run_script)
        self.task_pick_image_button.clicked.connect(self.select_task_image)
        self.task_use_latest_capture_button.clicked.connect(
            self.use_latest_capture_for_task
        )
        self.attach_images_button.clicked.connect(self.select_chat_images)
        self.clear_images_button.clicked.connect(self.clear_chat_images)
        self.send_button.clicked.connect(self.send_chat_message)
        self.start_local_task_button.clicked.connect(self.start_local_task_from_chat)
        self.new_conversation_button.clicked.connect(self.create_conversation)
        self.reload_conversations_button.clicked.connect(self.refresh_conversations)
        self.conversation_list.currentItemChanged.connect(self._on_conversation_selected)
        self.task_add_step_button.clicked.connect(self.add_task_step)
        self.task_clear_steps_button.clicked.connect(self.clear_task_steps)
        self.task_create_button.clicked.connect(self.create_task)
        self.task_add_agent_button.clicked.connect(self.add_agent_to_task)
        self.task_run_button.clicked.connect(self.run_selected_task)
        self.task_import_suggestions_button.clicked.connect(self.import_suggested_steps)
        self.task_approve_button.clicked.connect(self.approve_selected_task)
        self.task_deny_button.clicked.connect(self.deny_selected_task)
        self.task_prompt_button.clicked.connect(self.prompt_selected_task)
        self.task_refresh_button.clicked.connect(self.refresh_tasks)
        self.task_list.currentItemChanged.connect(self._on_task_selected)
        self.task_output_tabs.currentChanged.connect(self._on_task_tab_changed)
        self.task_output_tabs.tabCloseRequested.connect(self._close_task_tab)
        self.system_approve_button.clicked.connect(self.approve_system_script)
        self.system_deny_button.clicked.connect(self.deny_system_script)
        self.system_prompt_button.clicked.connect(self.prompt_system_script)

    def _load_stylesheet(self) -> None:
        qss_path = Path(__file__).with_name("style.qss")
        self.setStyleSheet(qss_path.read_text(encoding="utf-8"))

    def _queue_request(
        self,
        path: str,
        method: str,
        payload: dict | None,
        on_success,
        error_message: str,
    ) -> None:
        worker = ApiWorker(
            base_url=self.config.backend_url,
            path=path,
            method=method,
            payload=payload,
            timeout=self.config.request_timeout_seconds,
        )
        worker.succeeded.connect(on_success)
        worker.failed.connect(lambda err: self._on_request_failed(error_message, err))
        worker.finished.connect(lambda: self._cleanup_worker(worker))
        self._workers.append(worker)
        worker.start()

    def _cleanup_worker(self, worker: ApiWorker) -> None:
        if worker in self._workers:
            self._workers.remove(worker)
        worker.deleteLater()

    def _on_request_failed(self, context: str, error: str) -> None:
        self.send_button.setDisabled(False)
        self.start_local_task_button.setDisabled(False)
        self.statusBar().showMessage(f"{context}: {error}", 8000)

    def _apply_language(self) -> None:
        self.setWindowTitle(self._t("app_title"))
        self.hero_label.setText(self._t("app_title"))
        self.subtitle_label.setText(self._t("subtitle"))
        self.settings_button.setText(self._t("settings"))
        self.refresh_button.setText(self._t("refresh"))
        self.language_badge.setText(f"{self._t('language')}: {self.current_language}")
        self.runtime_group.setTitle(self._t("runtime"))
        self.backend_runtime_label.setText(self._t("backend"))
        self.provider_runtime_label.setText(self._t("provider"))
        self.mode_runtime_label.setText(self._t("control_mode"))
        self.controller_runtime_label.setText(self._t("controller"))
        self.chat_group.setTitle(self._t("conversation"))
        self.current_conversation_label.setText(self._t("conversation_none"))
        self.new_conversation_button.setText(self._t("new_conversation"))
        self.reload_conversations_button.setText(self._t("reload_history"))
        self.workspace_group.setTitle(self._t("workspace"))
        self.workspace_tabs.setTabText(0, self._t("tasks"))
        self.workspace_tabs.setTabText(1, self._t("perception"))
        self.workspace_tabs.setTabText(2, self._t("system"))
        self.workspace_tabs.setTabText(3, self._t("events"))
        self.task_saved_group.setTitle(self._t("task_saved"))
        self.task_builder_group.setTitle(self._t("task_builder"))
        self.task_output_group.setTitle(self._t("task_output"))
        self.task_title_label.setText(self._t("task_title"))
        self.task_instruction_label.setText(self._t("task_instruction"))
        self.task_instruction_input.setPlaceholderText(
            self._t("task_instruction_placeholder")
        )
        self.task_parent_label.setText(self._t("task_parent_agent"))
        self.task_autonomous_checkbox.setText(self._t("task_autonomous"))
        self.task_max_turns_label.setText(self._t("task_max_iterations"))
        self.task_step_label.setText(self._t("task_step"))
        self.task_step_value_label.setText(self._t("task_step_value"))
        self.task_step_value_input.setPlaceholderText(self._t("task_value_placeholder"))
        self.task_image_label.setText(self._t("task_image_source"))
        self.task_image_input.setPlaceholderText(self._t("task_image_placeholder"))
        self.task_pick_image_button.setText(self._t("task_pick_image"))
        self.task_use_latest_capture_button.setText(self._t("task_use_latest_capture"))
        self.task_add_step_button.setText(self._t("task_add_step"))
        self.task_clear_steps_button.setText(self._t("task_clear_steps"))
        self.task_create_button.setText(self._t("task_create"))
        self.task_add_agent_button.setText(self._t("task_add_agent"))
        self.task_run_button.setText(self._t("task_run_selected"))
        self.task_refresh_button.setText(self._t("task_reload"))
        self.task_import_suggestions_button.setText(self._t("task_import_suggestions"))
        self.task_approval_group.setTitle(self._t("task_approval_actions"))
        self.task_approve_button.setText(self._t("approval_allow"))
        self.task_deny_button.setText(self._t("approval_deny"))
        self.task_prompt_button.setText(self._t("approval_prompt"))
        self.task_approval_prompt_input.setPlaceholderText(
            self._t("task_approval_prompt_placeholder")
        )
        if self.task_parent_combo.count() == 0:
            self._populate_agent_parent_combo([])
        self.capture_path_title.setText(self._t("perception"))
        self.capture_button.setText(self._t("perception_capture"))
        self.ocr_button.setText(self._t("perception_ocr"))
        self.find_button.setText(self._t("perception_find"))
        self.find_text_input.setPlaceholderText(self._t("perception_find_placeholder"))
        self.perception_output.setPlaceholderText(self._t("perception_output_placeholder"))
        self.system_info_group.setTitle(self._t("system_info"))
        self.system_refresh_button.setText(self._t("system_refresh_info"))
        self.system_info_output.setPlaceholderText(self._t("system_output_placeholder"))
        self.system_script_group.setTitle(self._t("system_script"))
        self.system_runtime_label.setText(self._t("system_runtime"))
        self.system_runtime_combo.setItemText(0, self._t("runtime_auto"))
        self.system_runtime_combo.setItemText(1, self._t("runtime_python"))
        self.system_runtime_combo.setItemText(2, self._t("runtime_shell"))
        self.system_timeout_label.setText(self._t("system_timeout"))
        self.system_run_script_button.setText(self._t("system_run_script"))
        self.system_script_input.setPlaceholderText(self._t("system_script_placeholder"))
        self.system_approval_group.setTitle(self._t("system_inline_review"))
        self.system_approval_summary.setText(self._t("system_inline_review_placeholder"))
        self.system_prompt_input.setPlaceholderText(self._t("system_prompt_placeholder"))
        self.system_approve_button.setText(self._t("approval_allow"))
        self.system_deny_button.setText(self._t("approval_deny"))
        self.system_prompt_button.setText(self._t("approval_prompt"))
        self.system_script_output.setPlaceholderText(self._t("system_output_placeholder"))
        self._populate_step_combo()
        self._render_capture_path()
        self.prompt_input.setPlaceholderText(self._t("prompt_placeholder"))
        self.prompt_group.setTitle(self._t("prompt"))
        self.attach_images_button.setText(self._t("attach_images"))
        self.clear_images_button.setText(self._t("clear_images"))
        self.start_local_task_button.setText(self._t("start_local_task"))
        self.send_button.setText(self._t("send"))
        self._render_chat_attachments()
        self._render_task_output_placeholder()

    def _populate_step_combo(self) -> None:
        current = self.task_step_combo.currentData()
        items = [
            WorkflowStepType.DETECT_SYSTEM,
            WorkflowStepType.CAPTURE_SCREEN,
            WorkflowStepType.RUN_OCR,
            WorkflowStepType.FIND_TEXT,
            WorkflowStepType.ANALYZE_IMAGE,
            WorkflowStepType.EXECUTE_SCRIPT,
            WorkflowStepType.MOVE_MOUSE,
            WorkflowStepType.LEFT_CLICK,
            WorkflowStepType.TYPE_TEXT,
        ]
        self.task_step_combo.blockSignals(True)
        self.task_step_combo.clear()
        for item in items:
            self.task_step_combo.addItem(self._step_label(item), item.value)
        if current:
            index = self.task_step_combo.findData(current)
            if index >= 0:
                self.task_step_combo.setCurrentIndex(index)
        self.task_step_combo.blockSignals(False)

    def _step_label(self, step: WorkflowStepType) -> str:
        if self.current_language.lower().startswith("zh"):
            labels = {
                WorkflowStepType.DETECT_SYSTEM: "识别系统",
                WorkflowStepType.CAPTURE_SCREEN: "截图",
                WorkflowStepType.RUN_OCR: "运行 OCR",
                WorkflowStepType.FIND_TEXT: "查找文本",
                WorkflowStepType.EXECUTE_SCRIPT: "执行脚本",
                WorkflowStepType.MOVE_MOUSE: "移动鼠标",
                WorkflowStepType.LEFT_CLICK: "左键点击",
                WorkflowStepType.TYPE_TEXT: "输入文字",
            }
        else:
            labels = {
                WorkflowStepType.DETECT_SYSTEM: "Detect System",
                WorkflowStepType.CAPTURE_SCREEN: "Capture Screen",
                WorkflowStepType.RUN_OCR: "Run OCR",
                WorkflowStepType.FIND_TEXT: "Find Text",
                WorkflowStepType.EXECUTE_SCRIPT: "Execute Script",
                WorkflowStepType.MOVE_MOUSE: "Move Mouse",
                WorkflowStepType.LEFT_CLICK: "Left Click",
                WorkflowStepType.TYPE_TEXT: "Type Text",
            }
        return labels[step]

    def _step_label(self, step: WorkflowStepType) -> str:
        labels = {
            WorkflowStepType.DETECT_SYSTEM: "Detect System",
            WorkflowStepType.CAPTURE_SCREEN: "Capture Screen",
            WorkflowStepType.RUN_OCR: "Run OCR",
            WorkflowStepType.FIND_TEXT: "Find Text",
            WorkflowStepType.ANALYZE_IMAGE: "Analyze Image",
            WorkflowStepType.EXECUTE_SCRIPT: "Execute Script",
            WorkflowStepType.MOVE_MOUSE: "Move Mouse",
            WorkflowStepType.LEFT_CLICK: "Left Click",
            WorkflowStepType.TYPE_TEXT: "Type Text",
            WorkflowStepType.DELEGATE_AGENT: "Delegate Agent",
            WorkflowStepType.COMPLETE: "Complete",
        }
        return labels[step]

    def _provider_label(self, provider: str) -> str:
        mapping = {
            ProviderType.MOCK.value: self._t("provider_mock"),
            ProviderType.OPENAI_COMPATIBLE.value: self._t("provider_openai_compatible"),
            ProviderType.OLLAMA.value: self._t("provider_ollama"),
        }
        return mapping.get(provider, provider)

    def _control_mode_label(self, mode: str) -> str:
        mapping = {
            ControlMode.DENY.value: self._t("control_deny"),
            ControlMode.ASK.value: self._t("control_ask"),
            ControlMode.ALLOW_SESSION.value: self._t("control_allow_session"),
            ControlMode.ALLOW_ALWAYS.value: self._t("control_allow_always"),
        }
        return mapping.get(mode, mode)

    def open_settings_dialog(self) -> None:
        dialog = SettingsDialog(self._t, parent=self)
        self._active_settings_dialog = dialog
        dialog.set_snapshot(self._local_settings_snapshot())
        dialog.test_button.clicked.connect(
            lambda: self._test_provider_from_dialog(dialog)
        )
        dialog.test_all_button.clicked.connect(
            lambda: self._test_all_providers_from_dialog(dialog)
        )
        dialog.provider_combo.currentIndexChanged.connect(
            lambda: self._refresh_provider_capabilities_for_dialog(dialog)
        )
        dialog.model_input.editingFinished.connect(
            lambda: self._refresh_provider_capabilities_for_dialog(dialog)
        )
        dialog.allow_mock_fallback_checkbox.toggled.connect(
            lambda _: self._refresh_provider_capabilities_for_dialog(dialog)
        )
        self._last_provider_capabilities_payload = None
        self._last_provider_connectivity_payload = None
        self._refresh_provider_capabilities_for_dialog(dialog)
        dialog_result = dialog.exec()
        self._active_settings_dialog = None
        if dialog_result == QtWidgets.QDialog.DialogCode.Accepted:
            payload = dialog.build_apply_payload(self.config.request_timeout_seconds)
            self._queue_request(
                path="/api/settings/apply",
                method="POST",
                payload=payload,
                on_success=self._on_settings_saved,
                error_message="Saving settings failed",
            )

    def _local_settings_snapshot(self) -> dict:
        return {
            "provider": self.state.get_provider_settings().model_dump(mode="json"),
            "automation": self.state.get_automation_settings().model_dump(mode="json"),
            "ui": self.state.get_ui_state().model_dump(mode="json"),
        }

    def _test_provider_from_dialog(self, dialog: SettingsDialog) -> None:
        dialog.set_health_result(self._t("settings_not_tested"))
        self._queue_request(
            path="/api/provider/health",
            method="POST",
            payload=dialog.build_provider_payload(self.config.request_timeout_seconds),
            on_success=lambda payload: self._on_dialog_provider_health(dialog, payload),
            error_message="Provider test failed",
        )

    def _test_all_providers_from_dialog(self, dialog: SettingsDialog) -> None:
        dialog.set_connectivity_report(
            self._t("settings_connectivity_report_loading")
        )
        self._queue_request(
            path="/api/provider/health/all",
            method="POST",
            payload=dialog.build_provider_payload(self.config.request_timeout_seconds),
            on_success=lambda payload: self._on_dialog_provider_connectivity_sweep(
                dialog,
                payload,
            ),
            error_message="Provider sweep failed",
        )

    def _refresh_provider_capabilities_for_dialog(
        self,
        dialog: SettingsDialog,
    ) -> None:
        dialog.set_capabilities_result(self._t("settings_capabilities_loading"))
        self._queue_request(
            path="/api/provider/capabilities",
            method="POST",
            payload=dialog.build_provider_payload(self.config.request_timeout_seconds),
            on_success=lambda payload: self._on_dialog_provider_capabilities(dialog, payload),
            error_message="Provider capability preview failed",
        )

    def _on_dialog_provider_health(
        self,
        dialog: SettingsDialog,
        payload: dict,
    ) -> None:
        status = "Connected" if payload.get("ok") else "Needs attention"
        if self.current_language.lower().startswith("zh"):
            status = "已连接" if payload.get("ok") else "需要处理"
        dialog.set_health_result(f"{status} ({payload.get('latency_ms', 0)} ms)")
        self.statusBar().showMessage(payload.get("message", ""), 6000)

    def refresh_health(self) -> None:
        self._queue_request(
            path="/api/health",
            method="GET",
            payload=None,
            on_success=self._apply_health,
            error_message="Health refresh failed",
        )

    def refresh_settings(self) -> None:
        self._queue_request(
            path="/api/settings",
            method="GET",
            payload=None,
            on_success=self._apply_settings_snapshot,
            error_message="Settings refresh failed",
        )

    def refresh_conversations(self) -> None:
        self._queue_request(
            path="/api/conversations",
            method="GET",
            payload=None,
            on_success=self._apply_conversation_list,
            error_message="Conversation refresh failed",
        )

    def refresh_tasks(self) -> None:
        self._queue_request(
            path="/api/tasks",
            method="GET",
            payload=None,
            on_success=self._apply_task_list,
            error_message="Task refresh failed",
        )

    def save_current_conversation(self) -> None:
        payload = {"current_conversation_id": self.current_conversation_id}
        self._queue_request(
            path="/api/settings/ui",
            method="POST",
            payload=payload,
            on_success=self._on_ui_state_saved,
            error_message="Saving current conversation failed",
        )

    def create_conversation(self) -> None:
        self._queue_request(
            path="/api/conversations",
            method="POST",
            payload={},
            on_success=self._on_conversation_created,
            error_message="Creating conversation failed",
        )

    def load_conversation(self, conversation_id: str) -> None:
        encoded_id = quote(conversation_id, safe="")
        self._queue_request(
            path=f"/api/conversations/{encoded_id}",
            method="GET",
            payload=None,
            on_success=self._apply_conversation_history,
            error_message="Loading conversation failed",
        )

    def add_task_step(self) -> None:
        kind = self.task_step_combo.currentData()
        value = self.task_step_value_input.text().strip() or None
        if kind in {
            WorkflowStepType.FIND_TEXT.value,
            WorkflowStepType.TYPE_TEXT.value,
            WorkflowStepType.EXECUTE_SCRIPT.value,
        } and not value:
            self.statusBar().showMessage(self._t("status_enter_message"), 3000)
            return
        image_path = self.task_image_input.text().strip() or None
        if image_path and not Path(image_path).exists():
            self.statusBar().showMessage(self._t("status_task_image_missing"), 3000)
            return
        step = {"kind": kind}
        if value:
            step["text"] = value
        if kind in {
            WorkflowStepType.RUN_OCR.value,
            WorkflowStepType.FIND_TEXT.value,
            WorkflowStepType.ANALYZE_IMAGE.value,
        } and image_path:
            step["image_path"] = image_path
        self._draft_steps.append(step)
        self.task_step_value_input.clear()
        self._render_draft_steps()
        self.statusBar().showMessage(self._t("status_step_added"), 2500)

    def clear_task_steps(self) -> None:
        self._draft_steps.clear()
        self._render_draft_steps()
        self.statusBar().showMessage(self._t("status_steps_cleared"), 2500)

    def create_task(self) -> None:
        instruction = self.task_instruction_input.toPlainText().strip()
        autonomous = self.task_autonomous_checkbox.isChecked()
        if (not self._draft_steps and not instruction) or (
            not self._draft_steps and instruction and not autonomous
        ):
            self.statusBar().showMessage(
                self._t("status_task_instruction_required"),
                3000,
            )
            return
        payload = {
            "title": self.task_title_input.text().strip() or None,
            "instruction": instruction,
            "autonomous": autonomous,
            "max_iterations": self.task_max_turns_spin.value(),
            "preferred_language": self.current_language,
            "steps": self._draft_steps,
        }
        self._queue_request(
            path="/api/tasks",
            method="POST",
            payload=payload,
            on_success=self._on_task_created,
            error_message="Task creation failed",
        )

    def add_agent_to_task(self) -> None:
        if not self.selected_task_id:
            self.statusBar().showMessage(self._t("status_task_selected_required"), 3000)
            return
        instruction = self.task_instruction_input.toPlainText().strip()
        autonomous = self.task_autonomous_checkbox.isChecked()
        if (not self._draft_steps and not instruction) or (
            not self._draft_steps and instruction and not autonomous
        ):
            self.statusBar().showMessage(
                self._t("status_task_instruction_required"),
                3000,
            )
            return
        name = self.task_title_input.text().strip() or self._derive_agent_name(
            instruction
        )
        payload = {
            "name": name,
            "parent_agent_id": self.task_parent_combo.currentData(),
            "instruction": instruction,
            "autonomous": autonomous,
            "max_iterations": self.task_max_turns_spin.value(),
            "preferred_language": self.current_language,
            "steps": self._draft_steps,
        }
        encoded_id = quote(self.selected_task_id, safe="")
        self._queue_request(
            path=f"/api/tasks/{encoded_id}/agents",
            method="POST",
            payload=payload,
            on_success=self._on_agent_added,
            error_message="Adding agent failed",
        )

    def run_selected_task(self) -> None:
        if not self.selected_task_id:
            self.statusBar().showMessage(self._t("status_task_selected_required"), 3000)
            return
        encoded_id = quote(self.selected_task_id, safe="")
        self._queue_request(
            path=f"/api/tasks/{encoded_id}/run",
            method="POST",
            payload=None,
            on_success=self._on_task_run_finished,
            error_message="Task run failed",
        )

    def load_task(self, task_id: str) -> None:
        encoded_id = quote(task_id, safe="")
        self._queue_request(
            path=f"/api/tasks/{encoded_id}",
            method="GET",
            payload=None,
            on_success=self._apply_task_detail,
            error_message="Loading task failed",
        )

    def capture_screen(self) -> None:
        self._queue_request(
            path="/api/perception/capture",
            method="POST",
            payload={},
            on_success=self._on_capture_response,
            error_message="Screenshot capture failed",
        )

    def run_ocr(self) -> None:
        payload = {"image_path": self.latest_capture_path}
        self._queue_request(
            path="/api/perception/ocr",
            method="POST",
            payload=payload,
            on_success=self._on_ocr_response,
            error_message="OCR failed",
        )

    def find_text(self) -> None:
        query = self.find_text_input.text().strip()
        if not query:
            self.statusBar().showMessage(self._t("status_enter_find_text"), 3000)
            return

        payload = {
            "query": query,
            "image_path": self.latest_capture_path,
            "case_sensitive": False,
        }
        self._queue_request(
            path="/api/perception/find",
            method="POST",
            payload=payload,
            on_success=self._on_find_text_response,
            error_message="Text lookup failed",
        )

    def select_task_image(self) -> None:
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            self._t("task_pick_image"),
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)",
        )
        if not file_path:
            return
        self._task_selected_image_path = file_path
        self.task_image_input.setText(file_path)
        self.statusBar().showMessage(
            self._t("status_task_image_selected"),
            2500,
        )

    def use_latest_capture_for_task(self) -> None:
        if not self.latest_capture_path:
            self.statusBar().showMessage(self._t("status_task_image_missing"), 3000)
            return
        self._task_selected_image_path = self.latest_capture_path
        self.task_image_input.setText(self.latest_capture_path)
        self.statusBar().showMessage(self._t("status_task_image_latest"), 2500)

    def import_suggested_steps(self) -> None:
        suggestions = self._suggested_steps_from_task(self._last_task_payload or {})
        if not suggestions:
            self.statusBar().showMessage(
                self._t("status_no_suggested_steps"),
                3000,
            )
            return

        imported = [dict(step) for step in suggestions]
        self._draft_steps.extend(imported)
        for step in reversed(imported):
            image_path = step.get("image_path")
            if isinstance(image_path, str) and image_path:
                self.task_image_input.setText(image_path)
                self._task_selected_image_path = image_path
                break
        self._render_draft_steps()
        self.statusBar().showMessage(
            self._t("status_suggested_steps_imported", count=len(imported)),
            3000,
        )

    def select_chat_images(self) -> None:
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            self._t("attach_images"),
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)",
        )
        if not files:
            return
        selected = list(dict.fromkeys(self._selected_chat_attachments + files))
        self._selected_chat_attachments = selected
        self._render_chat_attachments()
        self.statusBar().showMessage(
            self._t("status_images_selected", count=len(self._selected_chat_attachments)),
            3000,
        )

    def clear_chat_images(self) -> None:
        self._selected_chat_attachments = []
        self._render_chat_attachments()
        self.statusBar().showMessage(self._t("status_images_cleared"), 2500)

    def _render_chat_attachments(self) -> None:
        if not self._selected_chat_attachments:
            self.chat_attachment_label.setText(self._t("chat_attachments_none"))
            self.clear_images_button.setEnabled(False)
            return

        names = [Path(path).name for path in self._selected_chat_attachments]
        preview = ", ".join(names[:3])
        if len(names) > 3:
            preview += f", +{len(names) - 3}"
        self.chat_attachment_label.setText(
            self._t(
                "chat_attachments_ready",
                count=len(names),
                names=preview,
            )
        )
        self.clear_images_button.setEnabled(True)

    def send_chat_message(self) -> None:
        message = self.prompt_input.toPlainText().strip()
        attachments = [
            {
                "name": Path(path).name,
                "media_type": mimetypes.guess_type(path)[0] or "image/png",
                "image_path": path,
            }
            for path in self._selected_chat_attachments
        ]
        if not message and not attachments:
            self.statusBar().showMessage(self._t("status_enter_message"), 3000)
            return

        self.prompt_input.clear()
        self.send_button.setDisabled(True)
        self.statusBar().showMessage(self._t("status_message_sending"), 3000)
        self._queue_request(
            path="/api/chat",
            method="POST",
            payload={
                "message": message,
                "conversation_id": self.current_conversation_id,
                "attachments": attachments,
            },
            on_success=self._on_chat_response,
            error_message="Chat request failed",
        )

    def start_local_task_from_chat(self) -> None:
        message = self.prompt_input.toPlainText().strip()
        attachments = list(self._selected_chat_attachments)
        if not message and not attachments:
            self.statusBar().showMessage(self._t("status_enter_message"), 3000)
            return
        self.start_local_task_button.setDisabled(True)
        self.statusBar().showMessage(self._t("status_local_task_starting"), 3000)
        self._queue_request(
            path="/api/tasks",
            method="POST",
            payload=self._build_local_chat_task_payload(
                message=message,
                attachments=attachments,
            ),
            on_success=self._on_local_chat_task_created,
            error_message="Creating local autonomous task failed",
        )

    def _build_local_chat_task_payload(
        self,
        *,
        message: str,
        attachments: list[str],
    ) -> dict:
        seed_steps: list[dict[str, object]] = []
        normalized_goal = message.strip() or "Inspect the provided image and continue the desktop task."
        for index, image_path in enumerate(attachments, start=1):
            seed_steps.append(
                {
                    "kind": WorkflowStepType.ANALYZE_IMAGE.value,
                    "label": f"Analyze Attachment {index}",
                    "text": normalized_goal,
                    "image_path": image_path,
                }
            )
        return {
            "title": None,
            "instruction": normalized_goal,
            "autonomous": True,
            "max_iterations": 8,
            "preferred_language": self.current_language,
            "steps": seed_steps,
            "model_assignment": {
                "provider": ProviderType.OLLAMA.value,
                "model": self.config.default_local_model,
                "base_url": self.config.ollama_base_url,
                "assignment_reason": "Default local autonomous task launched from chat.",
            },
        }

    def _on_local_chat_task_created(self, payload: dict) -> None:
        task_id = payload.get("task_id")
        if task_id:
            self.selected_task_id = task_id
        self.workspace_tabs.setCurrentIndex(0)
        self._apply_task_detail(payload)
        self.refresh_tasks()
        self.prompt_input.clear()
        self._selected_chat_attachments = []
        self._render_chat_attachments()
        self.statusBar().showMessage(self._t("status_local_task_created"), 2500)
        if not task_id:
            self.start_local_task_button.setDisabled(False)
            return
        encoded_id = quote(str(task_id), safe="")
        self._queue_request(
            path=f"/api/tasks/{encoded_id}/run",
            method="POST",
            payload=None,
            on_success=self._on_local_chat_task_run_finished,
            error_message="Starting local autonomous task failed",
        )

    def _on_local_chat_task_run_finished(self, payload: dict) -> None:
        self.start_local_task_button.setDisabled(False)
        self._on_task_run_finished(payload)
        self.workspace_tabs.setCurrentIndex(0)
        self.statusBar().showMessage(self._t("status_local_task_started"), 4000)

    def _apply_health(self, payload: dict) -> None:
        self.backend_status.setText(payload.get("status", "unknown"))
        self.provider_status.setText(self._provider_label(payload.get("provider", "unknown")))
        self.mode_status.setText(
            self._control_mode_label(payload.get("control_mode", "unknown"))
        )
        self.controller_status.setText(payload.get("input_controller", "unknown"))

    def _apply_settings_snapshot(self, payload: dict) -> None:
        self._hydrate_settings(payload)
        self._populate_events(payload.get("recent_events", []))
        self.refresh_conversations()
        self.refresh_tasks()
        self.refresh_system_info()
        self.statusBar().showMessage(self._t("status_refreshed"), 2000)

    def _hydrate_settings(self, payload: dict) -> None:
        ui_state = payload.get("ui", {})
        self.current_conversation_id = ui_state.get("current_conversation_id")
        self.latest_capture_path = ui_state.get("latest_capture_path")
        self.current_language = ui_state.get("language", SYSTEM_LANGUAGE) or SYSTEM_LANGUAGE
        self._apply_language()

    def _on_settings_saved(self, payload: dict) -> None:
        self._apply_settings_snapshot(payload)
        self.statusBar().showMessage(self._t("status_provider_saved"), 3000)

    def _on_ui_state_saved(self, payload: dict) -> None:
        self._hydrate_settings(payload)
        self._populate_events(payload.get("recent_events", []))

    def _on_conversation_created(self, payload: dict) -> None:
        conversation = payload.get("conversation", {})
        self.current_conversation_id = conversation.get("conversation_id")
        self._apply_conversation_history(payload)
        self.refresh_conversations()
        self.refresh_settings()
        self.statusBar().showMessage(self._t("status_conversation_created"), 3000)

    def _apply_conversation_list(self, payload: dict) -> None:
        conversations = payload.get("conversations", [])
        target_id = self.current_conversation_id
        if not target_id and conversations:
            target_id = conversations[0].get("conversation_id")

        self._suppress_conversation_selection = True
        self.conversation_list.clear()
        target_row: int | None = None

        for index, conversation in enumerate(conversations):
            conversation_id = conversation.get("conversation_id", "")
            title = conversation.get("title", "Untitled")
            count = conversation.get("message_count", 0)
            suffix = "条消息" if self.current_language.lower().startswith("zh") else "messages"
            item = QtWidgets.QListWidgetItem(f"{title}\n{count} {suffix}")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, conversation_id)
            item.setToolTip(conversation_id)
            self.conversation_list.addItem(item)
            if conversation_id == target_id:
                target_row = index

        if target_row is not None:
            self.conversation_list.setCurrentRow(target_row)
        elif conversations:
            fallback_id = conversations[0].get("conversation_id")
            self.current_conversation_id = fallback_id
            self.conversation_list.setCurrentRow(0)
            target_id = fallback_id
        else:
            self.current_conversation_id = None
            self.loaded_conversation_id = None
            self._render_empty_history()

        self._suppress_conversation_selection = False

        if target_id is not None and target_id != self.loaded_conversation_id:
            self.load_conversation(target_id)

    def _on_conversation_selected(
        self,
        current: QtWidgets.QListWidgetItem | None,
        _: QtWidgets.QListWidgetItem | None,
    ) -> None:
        if self._suppress_conversation_selection or current is None:
            return

        conversation_id = current.data(QtCore.Qt.ItemDataRole.UserRole)
        if not conversation_id:
            return

        self.current_conversation_id = conversation_id
        self.save_current_conversation()
        if self.loaded_conversation_id != conversation_id:
            self.load_conversation(conversation_id)

    def _apply_conversation_history(self, payload: dict) -> None:
        conversation = payload.get("conversation", {})
        messages = payload.get("messages", [])
        conversation_id = conversation.get("conversation_id")
        title = conversation.get("title", self._t("conversation"))

        self.current_conversation_id = conversation_id or self.current_conversation_id
        self.loaded_conversation_id = self.current_conversation_id
        count = conversation.get("message_count", len(messages))
        suffix = "条消息" if self.current_language.lower().startswith("zh") else "messages"
        self.chat_group.setTitle(f"{self._t('conversation')}: {title}")
        self.current_conversation_label.setText(f"{title} ({count} {suffix})")

        if not messages:
            empty_text = (
                "这个会话还是空的，发送消息后就会开始记录。"
                if self.current_language.lower().startswith("zh")
                else "This conversation is empty. Send a prompt to get started."
            )
            self.chat_view.setHtml(f"<p><i>{escape(empty_text)}</i></p>")
            return

        parts: list[str] = []
        for message in messages:
            speaker = self._role_label(message.get("role", "message"))
            content = escape(message.get("content", "")).replace("\n", "<br>")
            attachment_html = self._render_message_attachments_html(
                message.get("attachments", [])
            )
            body = content or "<i>[image message]</i>"
            parts.append(
                f"<p><b>{escape(speaker)}</b><br>{body}{attachment_html}</p>"
            )
        self.chat_view.setHtml("".join(parts))
        self.chat_view.moveCursor(QtGui.QTextCursor.MoveOperation.End)

    def _render_empty_history(self) -> None:
        self.chat_group.setTitle(self._t("conversation"))
        self.current_conversation_label.setText(self._t("conversation_none"))
        self.chat_view.setHtml(f"<p><i>{escape(self._t('conversation_empty'))}</i></p>")

    def _role_label(self, role: str) -> str:
        if self.current_language.lower().startswith("zh"):
            labels = {"system": "系统", "user": "你", "assistant": "助手"}
        else:
            labels = {"system": "System", "user": "You", "assistant": "Assistant"}
        return labels.get(role, role.title())

    def _render_message_attachments_html(self, attachments: list[dict]) -> str:
        if not attachments:
            return ""

        blocks: list[str] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            image_path = attachment.get("image_path")
            name = attachment.get("name") or (
                Path(image_path).name if isinstance(image_path, str) and image_path else "image"
            )
            if isinstance(image_path, str) and image_path and Path(image_path).exists():
                image_uri = Path(image_path).resolve().as_uri()
                blocks.append(
                    "".join(
                        [
                            "<div style='margin-top:8px'>",
                            f"<div><code>{escape(name)}</code></div>",
                            f"<img src='{escape(image_uri)}' style='max-width:320px; max-height:220px; margin-top:4px;' />",
                            "</div>",
                        ]
                    )
                )
            else:
                blocks.append(
                    f"<div style='margin-top:8px'><code>{escape(name)}</code></div>"
                )
        return "".join(blocks)

    def _render_draft_steps(self) -> None:
        self.task_draft_list.clear()
        for index, step in enumerate(self._draft_steps, start=1):
            kind = WorkflowStepType(step["kind"])
            suffix = self._step_suffix(step)
            self.task_draft_list.addItem(f"{index}. {self._step_label(kind)}{suffix}")

    def _step_suffix(self, step: dict) -> str:
        parts: list[str] = []
        value = step.get("text")
        image_path = step.get("image_path")
        if isinstance(value, str) and value:
            parts.append(value)
        if isinstance(image_path, str) and image_path:
            parts.append(f"image={Path(image_path).name}")
        if not parts:
            return ""
        return " - " + " | ".join(parts)

    def _suggested_steps_from_task(self, payload: dict) -> list[dict]:
        results = payload.get("results", [])
        for result in reversed(results):
            if result.get("kind") != WorkflowStepType.ANALYZE_IMAGE.value:
                continue
            output = result.get("output", {})
            if not isinstance(output, dict):
                continue
            suggestions = output.get("suggested_steps", [])
            if isinstance(suggestions, list) and suggestions:
                return [step for step in suggestions if isinstance(step, dict)]
        return []

    def _apply_task_list(self, payload: dict) -> None:
        tasks = payload.get("tasks", [])
        target_id = self.selected_task_id
        self._suppress_task_selection = True
        self.task_list.clear()
        target_row: int | None = None
        for index, task in enumerate(tasks):
            task_id = task.get("task_id", "")
            title = task.get("title", self._t("task_default_title"))
            status = task.get("status", "draft")
            agent_count = task.get("agent_count", 0)
            item = QtWidgets.QListWidgetItem(f"{title}\n{status} · {agent_count} agents")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, task_id)
            item.setToolTip(task.get("last_message") or task_id)
            self.task_list.addItem(item)
            if task_id == target_id:
                target_row = index
        if target_row is not None:
            self.task_list.setCurrentRow(target_row)
        elif tasks:
            self.selected_task_id = tasks[0].get("task_id")
            self.task_list.setCurrentRow(0)
        else:
            self.selected_task_id = None
            self.task_output.clear()
        self._suppress_task_selection = False
        if self.selected_task_id:
            self.load_task(self.selected_task_id)

    def _on_task_selected(
        self,
        current: QtWidgets.QListWidgetItem | None,
        _: QtWidgets.QListWidgetItem | None,
    ) -> None:
        if self._suppress_task_selection or current is None:
            return
        task_id = current.data(QtCore.Qt.ItemDataRole.UserRole)
        if not task_id:
            return
        self.selected_task_id = task_id
        self.load_task(task_id)

    def _apply_task_detail(self, payload: dict) -> None:
        self.selected_task_id = payload.get("task_id")
        self._populate_agent_parent_combo(payload.get("agents", []))
        lines = [
            f"Title: {payload.get('title', self._t('task_default_title'))}",
            f"Status: {payload.get('status', 'draft')}",
            f"Task Language: {payload.get('preferred_language', SYSTEM_LANGUAGE)}",
            f"Agents: {self._count_agents(payload.get('agents', []))}",
            "",
            "Steps:",
        ]
        for index, step in enumerate(payload.get("steps", []), start=1):
            kind = WorkflowStepType(step.get("kind", WorkflowStepType.CAPTURE_SCREEN.value))
            value = step.get("text")
            suffix = f" ({value})" if value else ""
            lines.append(f"{index}. {self._step_label(kind)}{suffix}")
        agent_lines = self._render_agent_lines(payload.get("agents", []))
        if agent_lines:
            lines.extend(["", "Agent Tree:"] + agent_lines)
        results = payload.get("results", [])
        if results:
            lines.extend(["", "Results:"])
            for result in results:
                marker = "成功" if self.current_language.lower().startswith("zh") and result.get("ok") else (
                    "失败" if self.current_language.lower().startswith("zh") else (
                        "OK" if result.get("ok") else "FAIL"
                    )
                )
                lines.append(
                    f"{result.get('index', 0)}. {marker} - {result.get('message', '')}"
                )
        self.task_output.setPlainText("\n".join(lines))

    def _bool_label(self, value: bool) -> str:
        if self.current_language.lower().startswith("zh"):
            return "鏄?" if value else "鍚?"
        return "Yes" if value else "No"

    def _derive_agent_name(self, instruction: str) -> str:
        normalized = " ".join(instruction.split()).strip()
        if not normalized:
            return "Subagent"
        return normalized[:32].rstrip() if len(normalized) > 32 else normalized

    def _render_agent_lines(
        self,
        agents: list[dict],
        depth: int = 0,
    ) -> list[str]:
        lines: list[str] = []
        for agent in agents:
            name = agent.get("name", "Agent")
            status = agent.get("status", "draft")
            step_count = len(agent.get("steps", []))
            indent = "  " * depth
            assignment = self._format_model_assignment(agent.get("model_assignment"))
            suffix = f" -> {assignment}" if assignment else ""
            lines.append(f"{indent}- {name} [{status}] ({step_count} steps){suffix}")
            lines.extend(self._render_agent_lines(agent.get("children", []), depth + 1))
        return lines

    def _format_model_assignment(self, assignment: dict | None) -> str | None:
        if not isinstance(assignment, dict):
            return None
        provider = str(assignment.get("provider") or "").strip()
        model = str(assignment.get("model") or "").strip()
        base_url = str(assignment.get("base_url") or "").strip()
        parts: list[str] = []
        if provider:
            parts.append(provider)
        if model:
            parts.append(model)
        text = " / ".join(parts)
        if base_url:
            text = f"{text} @ {base_url}" if text else base_url
        return text or None

    def _count_agents(self, agents: list[dict]) -> int:
        total = 0
        for agent in agents:
            total += 1
            total += self._count_agents(agent.get("children", []))
        return total

    def _populate_agent_parent_combo(self, agents: list[dict]) -> None:
        current_parent = self.task_parent_combo.currentData()
        self.task_parent_combo.blockSignals(True)
        self.task_parent_combo.clear()
        self.task_parent_combo.addItem("Task Root", None)
        for label, agent_id in self._collect_agent_options(agents):
            self.task_parent_combo.addItem(label, agent_id)
        if current_parent is not None:
            index = self.task_parent_combo.findData(current_parent)
            if index >= 0:
                self.task_parent_combo.setCurrentIndex(index)
        self.task_parent_combo.blockSignals(False)

    def _collect_agent_options(
        self,
        agents: list[dict],
        depth: int = 0,
    ) -> list[tuple[str, str]]:
        options: list[tuple[str, str]] = []
        for agent in agents:
            label = f"{'  ' * depth}{agent.get('name', 'Agent')}"
            agent_id = str(agent.get("agent_id", ""))
            if agent_id:
                options.append((label, agent_id))
            options.extend(self._collect_agent_options(agent.get("children", []), depth + 1))
        return options

    def _on_task_created(self, payload: dict) -> None:
        self.selected_task_id = payload.get("task_id")
        self.task_title_input.clear()
        self.task_instruction_input.clear()
        self._draft_steps.clear()
        self._render_draft_steps()
        self._apply_task_detail(payload)
        self.refresh_tasks()
        self.statusBar().showMessage(self._t("status_task_created"), 3000)

    def _on_agent_added(self, payload: dict) -> None:
        self.task_title_input.clear()
        self._draft_steps.clear()
        self._render_draft_steps()
        self._apply_task_detail(payload)
        self.refresh_tasks()
        self.statusBar().showMessage("Agent added.", 3000)

    def _on_task_run_finished(self, payload: dict) -> None:
        self._apply_task_detail(payload.get("task", {}))
        self.refresh_tasks()
        self.refresh_settings()
        self.statusBar().showMessage(self._t("status_task_running"), 3000)

    def _on_capture_response(self, payload: dict) -> None:
        self.latest_capture_path = payload.get("image_path")
        self._render_capture_path()
        self.perception_output.setPlainText(
            "\n".join(
                [
                    payload.get("message", ""),
                    f"Path: {self.latest_capture_path or 'n/a'}",
                    f"Size: {payload.get('width', 0)} x {payload.get('height', 0)}",
                ]
            )
        )
        self.refresh_settings()
        self.statusBar().showMessage(payload.get("message", ""), 5000)

    def _on_ocr_response(self, payload: dict) -> None:
        lines = payload.get("lines", [])
        header = [
            f"Engine: {payload.get('engine', 'unknown')}",
            payload.get("message", ""),
            f"Image: {payload.get('image_path', 'n/a')}",
            "",
        ]
        rendered_lines = []
        for index, line in enumerate(lines, start=1):
            rendered_lines.append(
                f"{index}. {line.get('text', '')} (score={line.get('score', 0.0):.2f})"
            )
        self.perception_output.setPlainText("\n".join(header + rendered_lines))
        self.statusBar().showMessage(payload.get("message", ""), 5000)

    def _on_find_text_response(self, payload: dict) -> None:
        matches = payload.get("matches", [])
        header = [
            payload.get("message", ""),
            f"Image: {payload.get('image_path', 'n/a')}",
            "",
        ]
        rendered_matches = []
        for index, match in enumerate(matches, start=1):
            rendered_matches.append(
                f"{index}. {match.get('text', '')} -> ({match.get('center_x', 0)}, {match.get('center_y', 0)}) score={match.get('score', 0.0):.2f}"
            )
        self.perception_output.setPlainText("\n".join(header + rendered_matches))
        self.statusBar().showMessage(payload.get("message", ""), 5000)

    def _on_chat_response(self, payload: dict) -> None:
        self.send_button.setDisabled(False)
        self._selected_chat_attachments = []
        self._render_chat_attachments()
        conversation_id = payload.get("conversation_id")
        if conversation_id:
            self.current_conversation_id = conversation_id
            self.loaded_conversation_id = None
            self.load_conversation(conversation_id)
        self.refresh_conversations()
        self.refresh_settings()

        provider = payload.get("provider", "assistant")
        model = payload.get("model", "unknown")
        latency = payload.get("latency_ms", 0)
        self.statusBar().showMessage(
            f"Response ready from {provider}/{model} in {latency} ms.",
            5000,
        )

    def _populate_events(self, events: list[str]) -> None:
        self.events_list.clear()
        self.events_list.addItems(events)

    def _render_capture_path(self) -> None:
        if not self.latest_capture_path:
            self.capture_path_label.setText(self._t("perception_no_capture"))
            return
        path = Path(self.latest_capture_path)
        self.capture_path_label.setText(path.name)
        self.capture_path_label.setToolTip(self.latest_capture_path)

    def _on_dialog_provider_health(
        self,
        dialog: SettingsDialog,
        payload: dict,
    ) -> None:
        status = (
            self._t("provider_connected")
            if payload.get("ok")
            else self._t("provider_needs_attention")
        )
        dialog.set_health_result(f"{status} ({payload.get('latency_ms', 0)} ms)")
        self.statusBar().showMessage(payload.get("message", ""), 6000)

    def _on_dialog_provider_capabilities(
        self,
        dialog: SettingsDialog,
        payload: dict,
    ) -> None:
        if dialog is not self._active_settings_dialog:
            return
        self._last_provider_capabilities_payload = payload

        current_provider = payload.get("current_provider", ProviderType.MOCK.value)
        current_model = payload.get("current_model", "unknown")
        allow_mock_fallback = payload.get("allow_mock_fallback", True)
        current_profile = next(
            (
                profile
                for profile in payload.get("capabilities", [])
                if profile.get("provider") == current_provider
            ),
            None,
        )
        if current_profile is None:
            dialog.set_capabilities_result(self._t("settings_capabilities_loading"))
            return

        runtime = self._runtime_label_for_profile(current_profile)
        features = ", ".join(self._feature_labels_for_profile(current_profile))
        alternative_labels = [
            self._provider_label(profile.get("provider", "unknown"))
            for profile in payload.get("capabilities", [])
            if profile.get("provider") != current_provider
        ]
        lines = [
            f"{self._t('provider')}: {current_profile.get('label', current_provider)}",
            f"{self._t('settings_model')}: {current_model}",
            f"Runtime: {runtime}",
            f"Features: {features}",
            f"Fallback: {'on' if allow_mock_fallback else 'off'}",
            "",
            current_profile.get("routing_hint", ""),
        ]
        if alternative_labels:
            lines.extend(["", f"Alternatives: {', '.join(alternative_labels)}"])
        dialog.set_capabilities_result("\n".join(line for line in lines if line))

    def _on_dialog_provider_connectivity_sweep(
        self,
        dialog: SettingsDialog,
        payload: dict,
    ) -> None:
        if dialog is not self._active_settings_dialog:
            return
        self._last_provider_connectivity_payload = payload
        dialog.set_connectivity_report(
            self._render_provider_connectivity_report(payload)
        )
        total = len(payload.get("results", []))
        ready = payload.get("ok_count", 0)
        reachable = payload.get("reachable_count", 0)
        self.statusBar().showMessage(
            f"Provider sweep finished: {ready}/{total} ready, {reachable}/{total} reachable.",
            6000,
        )

    def _render_provider_connectivity_report(self, payload: dict) -> str:
        lines = [
            (
                f"Current route: {payload.get('current_provider', ProviderType.MOCK.value)}"
                f" / {payload.get('current_model', 'unknown')}"
            ),
            (
                f"Ready routes: {payload.get('ok_count', 0)} / "
                f"{len(payload.get('results', []))}"
            ),
        ]
        for item in payload.get("results", []):
            provider = item.get("provider", "unknown")
            model = item.get("model", "unknown")
            latency = item.get("latency_ms", 0)
            route_status = "ready" if item.get("ok") else "needs_attention"
            if not item.get("selected_model_available", True):
                route_status = "model_missing"
            lines.extend(
                [
                    "",
                    f"[{provider}] {route_status} / {latency} ms",
                    f"Model: {model}",
                    f"Base URL: {item.get('base_url', '') or 'n/a'}",
                    f"Message: {item.get('message', '')}",
                ]
            )
            models = item.get("discovered_models", [])
            if isinstance(models, list) and models:
                preview = ", ".join(str(name) for name in models[:6])
                lines.append(f"Discovered: {preview}")
        return "\n".join(lines)

    def _runtime_label_for_profile(self, profile: dict) -> str:
        local_runtime = bool(profile.get("local_runtime"))
        remote_runtime = bool(profile.get("remote_runtime"))
        if local_runtime and remote_runtime:
            return "hybrid"
        if local_runtime:
            return "local"
        if remote_runtime:
            return "remote"
        return "unknown"

    def _feature_labels_for_profile(self, profile: dict) -> list[str]:
        labels: list[str] = []
        if profile.get("supports_text", True):
            labels.append("text")
        if profile.get("supports_vision"):
            labels.append("vision")
        if profile.get("supports_tools"):
            labels.append("tools")
        if profile.get("supports_model_listing"):
            labels.append("model listing")
        return labels or ["basic"]

    def _hydrate_settings(self, payload: dict) -> None:
        automation = payload.get("automation", {})
        ui_state = payload.get("ui", {})
        self.current_conversation_id = ui_state.get("current_conversation_id")
        self.latest_capture_path = ui_state.get("latest_capture_path")
        self.current_language = ui_state.get("language", SYSTEM_LANGUAGE) or SYSTEM_LANGUAGE
        self.output_mode = ui_state.get("output_mode", OutputMode.FINAL_ONLY.value)
        self.approval_timeout_seconds = int(
            automation.get("approval_timeout_seconds", 60)
        )
        self.approval_timeout_action = str(
            automation.get("approval_timeout_action", ApprovalTimeoutAction.DENY.value)
        )
        self.approval_timeout_prompt = str(
            automation.get(
                "approval_timeout_prompt",
                "Approval timed out. Continue with a safer alternative and avoid the blocked high-risk action.",
            )
        )
        self._apply_language()
        self._rerender_cached_outputs()

    def _rerender_cached_outputs(self) -> None:
        if self._last_task_payload is not None:
            self._render_task_output(self._last_task_payload)
        if self._last_system_info is not None:
            self.system_info_output.setPlainText(
                self._render_system_info_output(self._last_system_info)
            )
        if self._last_perception_payload is None or self._last_perception_kind is None:
            return
        if self._last_perception_kind == "capture":
            self.perception_output.setPlainText(
                self._render_capture_output(self._last_perception_payload)
            )
        elif self._last_perception_kind == "ocr":
            self.perception_output.setPlainText(
                self._render_ocr_output(self._last_perception_payload)
            )
        elif self._last_perception_kind == "find":
            self.perception_output.setPlainText(
                self._render_find_text_output(self._last_perception_payload)
            )

    def _apply_task_list(self, payload: dict) -> None:
        tasks = payload.get("tasks", [])
        target_id = self.selected_task_id
        self._suppress_task_selection = True
        self.task_list.clear()
        target_row: int | None = None
        for index, task in enumerate(tasks):
            task_id = task.get("task_id", "")
            title = task.get("title", self._t("task_default_title"))
            status = task.get("status", "draft")
            agent_count = task.get("agent_count", 0)
            item = QtWidgets.QListWidgetItem(
                f"{title}\n{status} · {agent_count} {self._t('task_label_agents')}"
            )
            item.setData(QtCore.Qt.ItemDataRole.UserRole, task_id)
            item.setToolTip(task.get("last_message") or task_id)
            self.task_list.addItem(item)
            if task_id == target_id:
                target_row = index
        if target_row is not None:
            self.task_list.setCurrentRow(target_row)
        elif tasks:
            self.selected_task_id = tasks[0].get("task_id")
            self.task_list.setCurrentRow(0)
        else:
            self.selected_task_id = None
            self._last_task_payload = None
            self.task_approve_button.setEnabled(False)
            self.task_import_suggestions_button.setEnabled(False)
            self.task_output.clear()
        self._suppress_task_selection = False
        if self.selected_task_id:
            self.load_task(self.selected_task_id)

    def _apply_task_detail(self, payload: dict) -> None:
        self.selected_task_id = payload.get("task_id")
        self._last_task_payload = payload
        self._populate_agent_parent_combo(payload.get("agents", []))
        self._render_task_output(payload)

    def _render_task_output(self, payload: dict) -> None:
        lines = [
            f"{self._t('task_label_title')}: {payload.get('title', self._t('task_default_title'))}",
            f"{self._t('task_label_status')}: {payload.get('status', 'draft')}",
            f"{self._t('task_label_task_language')}: {payload.get('preferred_language', SYSTEM_LANGUAGE)}",
            f"{self._t('task_label_agents')}: {self._count_agents(payload.get('agents', []))}",
        ]
        root_agents = payload.get("agents", [])
        if root_agents:
            root_agent = root_agents[0]
            lines.append(
                f"{self._t('task_label_autonomous')}: {self._bool_label(bool(root_agent.get('autonomous')))}"
            )
            lines.append(
                f"{self._t('task_label_max_iterations')}: {root_agent.get('max_iterations', 8)}"
            )
        latest_message = payload.get("last_message") or self._latest_task_message(
            payload.get("results", [])
        )
        if latest_message:
            lines.append(f"{self._t('task_label_latest_message')}: {latest_message}")
        pending = payload.get("pending_approval") or {}
        if pending:
            lines.append(
                f"{self._t('task_pending_approval')}: "
                f"{pending.get('agent_name', 'Agent')} step {pending.get('step_index', '?')}"
            )
            lines.append(f"{self._t('task_risk')}: {pending.get('risk_level', 'unknown')}")
        self.task_approve_button.setEnabled(bool(pending))
        suggested_steps = self._suggested_steps_from_task(payload)
        self.task_import_suggestions_button.setEnabled(bool(suggested_steps))

        if self.output_mode == OutputMode.FINAL_ONLY.value:
            if suggested_steps:
                lines.append(
                    f"{self._t('task_suggested_steps')}: {len(suggested_steps)}"
                )
            self.task_output.setPlainText("\n".join(lines))
            return

        lines.extend(["", f"{self._t('task_heading_steps')}:"])
        for index, step in enumerate(payload.get("steps", []), start=1):
            kind = WorkflowStepType(step.get("kind", WorkflowStepType.CAPTURE_SCREEN.value))
            suffix = self._step_suffix(step)
            lines.append(f"{index}. {self._step_label(kind)}{suffix}")

        agent_lines = self._render_agent_lines(payload.get("agents", []))
        if agent_lines:
            lines.extend(["", f"{self._t('task_heading_agent_tree')}:"] + agent_lines)

        results = payload.get("results", [])
        if results:
            lines.extend(["", f"{self._t('task_heading_results')}:"])
            for result in results:
                lines.append(
                    f"{result.get('index', 0)}. {self._result_marker(result)} - {result.get('message', '')}"
                )
                output = result.get("output", {})
                if (
                    result.get("kind") == WorkflowStepType.ANALYZE_IMAGE.value
                    and isinstance(output, dict)
                    and output.get("content")
                ):
                    lines.append(output.get("content", ""))
                    suggestions = output.get("suggested_steps", [])
                    if isinstance(suggestions, list) and suggestions:
                        lines.append(f"{self._t('task_suggested_steps')}:")
                        for suggestion_index, suggestion in enumerate(suggestions, start=1):
                            if isinstance(suggestion, dict):
                                kind_value = suggestion.get(
                                    "kind",
                                    WorkflowStepType.CAPTURE_SCREEN.value,
                                )
                                kind = WorkflowStepType(kind_value)
                                lines.append(
                                    f"  {suggestion_index}. {self._step_label(kind)}"
                                    f"{self._step_suffix(suggestion)}"
                                )
        if pending:
            lines.extend(
                [
                    "",
                    f"{self._t('task_pending_details')}:",
                    pending.get("summary", ""),
                    *pending.get("warnings", []),
                ]
            )

        self.task_output.setPlainText("\n".join(lines))

    def _latest_task_message(self, results: list[dict]) -> str | None:
        if not results:
            return None
        latest = results[-1]
        agent_name = latest.get("agent_name")
        message = latest.get("message", "")
        if agent_name:
            return f"{agent_name}: {message}"
        return message or None

    def _result_marker(self, result: dict) -> str:
        if result.get("ok"):
            return "成功" if self.current_language.lower().startswith("zh") else "OK"
        return "失败" if self.current_language.lower().startswith("zh") else "FAIL"

    def _render_agent_lines(
        self,
        agents: list[dict],
        depth: int = 0,
    ) -> list[str]:
        lines: list[str] = []
        for agent in agents:
            name = agent.get("name", "Agent")
            status = agent.get("status", "draft")
            step_count = len(agent.get("steps", []))
            indent = "  " * depth
            lines.append(f"{indent}- {name} [{status}] ({step_count} steps)")
            lines.extend(self._render_agent_lines(agent.get("children", []), depth + 1))
        return lines

    def _populate_agent_parent_combo(self, agents: list[dict]) -> None:
        current_parent = self.task_parent_combo.currentData()
        self.task_parent_combo.blockSignals(True)
        self.task_parent_combo.clear()
        self.task_parent_combo.addItem(self._t("task_root"), None)
        for label, agent_id in self._collect_agent_options(agents):
            self.task_parent_combo.addItem(label, agent_id)
        if current_parent is not None:
            index = self.task_parent_combo.findData(current_parent)
            if index >= 0:
                self.task_parent_combo.setCurrentIndex(index)
        self.task_parent_combo.blockSignals(False)

    def _on_agent_added(self, payload: dict) -> None:
        self.task_title_input.clear()
        self.task_instruction_input.clear()
        self._draft_steps.clear()
        self._render_draft_steps()
        self._apply_task_detail(payload)
        self.refresh_tasks()
        self.statusBar().showMessage(self._t("status_agent_added"), 3000)

    def _on_capture_response(self, payload: dict) -> None:
        self.latest_capture_path = payload.get("image_path")
        self._last_perception_kind = "capture"
        self._last_perception_payload = payload
        self._render_capture_path()
        self.perception_output.setPlainText(self._render_capture_output(payload))
        self.refresh_settings()
        self.statusBar().showMessage(payload.get("message", ""), 5000)

    def _render_capture_output(self, payload: dict) -> str:
        lines = [
            payload.get("message", ""),
            f"Path: {payload.get('image_path') or self.latest_capture_path or 'n/a'}",
        ]
        if self.output_mode == OutputMode.STEP_SUMMARY.value:
            lines.append(f"Size: {payload.get('width', 0)} x {payload.get('height', 0)}")
        return "\n".join(lines)

    def _on_ocr_response(self, payload: dict) -> None:
        self._last_perception_kind = "ocr"
        self._last_perception_payload = payload
        self.perception_output.setPlainText(self._render_ocr_output(payload))
        self.statusBar().showMessage(payload.get("message", ""), 5000)

    def _render_ocr_output(self, payload: dict) -> str:
        lines = payload.get("lines", [])
        if self.output_mode == OutputMode.FINAL_ONLY.value:
            summary = [
                payload.get("message", ""),
                f"Image: {payload.get('image_path', 'n/a')}",
                f"Lines: {len(lines)}",
            ]
            if lines:
                summary.append(f"Preview: {lines[0].get('text', '')}")
            return "\n".join(summary)

        header = [
            f"Engine: {payload.get('engine', 'unknown')}",
            payload.get("message", ""),
            f"Image: {payload.get('image_path', 'n/a')}",
            "",
        ]
        rendered_lines = [
            f"{index}. {line.get('text', '')} (score={line.get('score', 0.0):.2f})"
            for index, line in enumerate(lines, start=1)
        ]
        return "\n".join(header + rendered_lines)

    def _on_find_text_response(self, payload: dict) -> None:
        self._last_perception_kind = "find"
        self._last_perception_payload = payload
        self.perception_output.setPlainText(self._render_find_text_output(payload))
        self.statusBar().showMessage(payload.get("message", ""), 5000)

    def _render_find_text_output(self, payload: dict) -> str:
        matches = payload.get("matches", [])
        if self.output_mode == OutputMode.FINAL_ONLY.value:
            summary = [
                payload.get("message", ""),
                f"Image: {payload.get('image_path', 'n/a')}",
            ]
            if matches:
                top_match = matches[0]
                summary.append(
                    "Top Match: "
                    f"{top_match.get('text', '')} -> "
                    f"({top_match.get('center_x', 0)}, {top_match.get('center_y', 0)})"
                )
            return "\n".join(summary)

        header = [
            payload.get("message", ""),
            f"Image: {payload.get('image_path', 'n/a')}",
            "",
        ]
        rendered_matches = [
            (
                f"{index}. {match.get('text', '')} -> "
                f"({match.get('center_x', 0)}, {match.get('center_y', 0)}) "
                f"score={match.get('score', 0.0):.2f}"
            )
            for index, match in enumerate(matches, start=1)
        ]
        return "\n".join(header + rendered_matches)

    def _on_chat_response(self, payload: dict) -> None:
        self.send_button.setDisabled(False)
        self._selected_chat_attachments = []
        self._render_chat_attachments()
        conversation_id = payload.get("conversation_id")
        if conversation_id:
            self.current_conversation_id = conversation_id
            self.loaded_conversation_id = None
            self.load_conversation(conversation_id)
        self.refresh_conversations()
        self.refresh_settings()

        provider = payload.get("provider", "assistant")
        model = payload.get("model", "unknown")
        latency = payload.get("latency_ms", 0)
        status_message = f"Response ready from {provider}/{model} in {latency} ms."
        attachment_count = payload.get("attachment_count", 0)
        if attachment_count:
            status_message += f" Vision input: {attachment_count} image(s)."
        if payload.get("fallback_used"):
            attempted = " -> ".join(payload.get("attempted_providers", []))
            status_message += f" Fallback route: {attempted}."
        self.statusBar().showMessage(status_message, 5000)

    def refresh_system_info(self) -> None:
        self._queue_request(
            path="/api/system/info",
            method="GET",
            payload=None,
            on_success=self._on_system_info_response,
            error_message="System info refresh failed",
        )

    def review_and_run_script(self) -> None:
        script = self.system_script_input.toPlainText().strip()
        if not script:
            self.statusBar().showMessage(self._t("status_enter_message"), 3000)
            return
        payload = {
            "script": script,
            "runtime": self.system_runtime_combo.currentData(),
            "timeout_seconds": self.system_timeout_spin.value(),
            "approval_timeout_seconds": self.approval_timeout_seconds,
        }
        self._queue_request(
            path="/api/system/script/prepare",
            method="POST",
            payload=payload,
            on_success=self._on_script_prepared,
            error_message="Script review failed",
        )

    def _on_system_info_response(self, payload: dict) -> None:
        self._last_system_info = payload
        self.system_info_output.setPlainText(self._render_system_info_output(payload))
        self.statusBar().showMessage(self._t("status_system_info_refreshed"), 3000)

    def _render_system_info_output(self, payload: dict) -> str:
        return "\n".join(
            [
                f"{self._t('script_os_prefix')}: {payload.get('os_name', 'Unknown')} {payload.get('os_release', '')}".strip(),
                f"Version: {payload.get('os_version', 'Unknown')}",
                f"Machine: {payload.get('machine', 'Unknown')}",
                f"Python: {payload.get('python_version', 'Unknown')}",
                f"{self._t('script_runtime_prefix')}: {payload.get('preferred_script_runtime', 'auto')}",
                f"Shell: {payload.get('preferred_shell', 'unknown')}",
                f"Screenshot: {payload.get('screenshot_backend', 'unknown')}",
                f"OCR: {payload.get('ocr_backend', 'unknown')}",
            ]
        )

    def _on_script_prepared(self, payload: dict) -> None:
        self._pending_script_confirmation_id = payload.get("confirmation_id")
        warning_lines = payload.get("warnings", [])
        message_lines = [
            self._t("script_review_prompt"),
            "",
            f"{self._t('script_os_prefix')}: {payload.get('os_name', 'Unknown')}",
            f"{self._t('script_runtime_prefix')}: {payload.get('runtime', 'auto')} / {payload.get('preferred_shell', 'unknown')}",
            f"Risk: {payload.get('risk_level', 'unknown')}",
        ]
        if warning_lines:
            message_lines.extend(
                [
                    "",
                    f"{self._t('script_warning_prefix')}:",
                    *warning_lines,
                ]
            )
        preview = payload.get("preview", "")
        if preview:
            message_lines.extend(
                [
                    "",
                    f"{self._t('script_preview_prefix')}:",
                    preview,
                ]
            )

        choice = QtWidgets.QMessageBox.warning(
            self,
            self._t("script_review_title"),
            "\n".join(message_lines),
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if choice != QtWidgets.QMessageBox.StandardButton.Yes:
            self.statusBar().showMessage(self._t("status_script_cancelled"), 3000)
            return

        self._queue_request(
            path="/api/system/script/execute",
            method="POST",
            payload={
                "confirmation_id": self._pending_script_confirmation_id,
                "confirm": True,
            },
            on_success=self._on_script_executed,
            error_message="Script execution failed",
        )

    def _on_script_executed(self, payload: dict) -> None:
        stdout = payload.get("stdout", "")
        stderr = payload.get("stderr", "")
        sections = [
            payload.get("summary", ""),
            f"{self._t('script_runtime_prefix')}: {payload.get('runtime', 'auto')} / {payload.get('preferred_shell', 'unknown')}",
            f"Exit Code: {payload.get('exit_code', 'n/a')}",
        ]
        if payload.get("timed_out"):
            sections.append("Timed Out: True")
        if stdout:
            sections.extend(["", "STDOUT:", stdout])
        if stderr:
            sections.extend(["", "STDERR:", stderr])
        self.system_script_output.setPlainText("\n".join(sections))
        self.statusBar().showMessage(self._t("status_script_running"), 4000)

    def approve_selected_task(self) -> None:
        self._submit_task_approval(ApprovalTimeoutAction.ALLOW)

    def deny_selected_task(self) -> None:
        self._submit_task_approval(ApprovalTimeoutAction.DENY)

    def prompt_selected_task(self) -> None:
        self._submit_task_approval(ApprovalTimeoutAction.PROMPT)

    def _submit_task_approval(self, decision: ApprovalTimeoutAction) -> None:
        if not self.selected_task_id:
            self.statusBar().showMessage(self._t("status_task_selected_required"), 3000)
            return
        pending = self._task_pending_approval or {}
        if not pending:
            self.statusBar().showMessage(self._t("status_task_selected_required"), 3000)
            return
        extra_prompt: str | None = None
        if decision == ApprovalTimeoutAction.PROMPT:
            extra_prompt = self.task_approval_prompt_input.toPlainText().strip()
            if not extra_prompt:
                self.statusBar().showMessage(self._t("status_task_prompt_required"), 3000)
                return
        encoded_id = quote(self.selected_task_id, safe="")
        self._queue_request(
            path=f"/api/tasks/{encoded_id}/approve",
            method="POST",
            payload={
                "decision": decision.value,
                "extra_prompt": extra_prompt,
            },
            on_success=lambda payload, current=decision: self._on_task_approval_finished(
                payload,
                current,
            ),
            error_message="Task approval failed",
        )

    def _on_task_approval_finished(
        self,
        payload: dict,
        decision: ApprovalTimeoutAction,
    ) -> None:
        self._on_task_run_finished(payload)
        messages = {
            ApprovalTimeoutAction.ALLOW: self._t("status_task_approval_sent"),
            ApprovalTimeoutAction.DENY: self._t("status_task_denied"),
            ApprovalTimeoutAction.PROMPT: self._t("status_task_prompt_sent"),
        }
        self.statusBar().showMessage(messages[decision], 3000)

    def _apply_task_list(self, payload: dict) -> None:
        tasks = payload.get("tasks", [])
        target_id = self.selected_task_id
        task_ids = {
            str(task.get("task_id"))
            for task in tasks
            if isinstance(task, dict) and task.get("task_id")
        }
        self._prune_task_output_tabs(task_ids)
        self._suppress_task_selection = True
        self.task_list.clear()
        target_row: int | None = None
        for index, task in enumerate(tasks):
            task_id = task.get("task_id", "")
            title = task.get("title", self._t("task_default_title"))
            status = task.get("status", "draft")
            agent_count = task.get("agent_count", 0)
            item = QtWidgets.QListWidgetItem(
                f"{title}\n{status} 路 {agent_count} {self._t('task_label_agents')}"
            )
            item.setData(QtCore.Qt.ItemDataRole.UserRole, task_id)
            item.setToolTip(task.get("last_message") or task_id)
            self.task_list.addItem(item)
            if task_id == target_id:
                target_row = index
        if target_row is not None:
            self.task_list.setCurrentRow(target_row)
        elif tasks:
            self.selected_task_id = tasks[0].get("task_id")
            self.task_list.setCurrentRow(0)
        else:
            self.selected_task_id = None
            self._last_task_payload = None
            self._set_task_pending_approval(None)
            self._render_task_output_placeholder(force=True)
        self._suppress_task_selection = False
        if self.selected_task_id:
            self.load_task(self.selected_task_id)

    def _on_task_selected(
        self,
        current: QtWidgets.QListWidgetItem | None,
        _: QtWidgets.QListWidgetItem | None,
    ) -> None:
        if self._suppress_task_selection or current is None:
            return
        task_id = current.data(QtCore.Qt.ItemDataRole.UserRole)
        if not task_id:
            return
        self.selected_task_id = task_id
        index = self._find_task_output_tab_index(task_id)
        if index >= 0 and self.task_output_tabs.currentIndex() != index:
            self.task_output_tabs.setCurrentIndex(index)
        self.load_task(task_id)

    def _on_task_tab_changed(self, index: int) -> None:
        if index < 0:
            return
        widget = self.task_output_tabs.widget(index)
        if widget is None:
            return
        task_id = widget.property("task_id")
        if not task_id or task_id == self.selected_task_id:
            return
        self.selected_task_id = str(task_id)
        self._select_task_in_list(self.selected_task_id)
        self.load_task(self.selected_task_id)

    def _close_task_tab(self, index: int) -> None:
        widget = self.task_output_tabs.widget(index)
        if widget is None:
            return
        task_id = widget.property("task_id")
        self.task_output_tabs.removeTab(index)
        widget.deleteLater()
        if self.task_output_tabs.count() == 0:
            self.selected_task_id = None
            self._set_task_pending_approval(None)
            self._render_task_output_placeholder(force=True)
            return
        current = self.task_output_tabs.currentWidget()
        if current is None:
            return
        current_task_id = current.property("task_id")
        if current_task_id:
            self.selected_task_id = str(current_task_id)
            self._select_task_in_list(self.selected_task_id)
        elif task_id == self.selected_task_id:
            self.selected_task_id = None

    def _apply_task_detail(self, payload: dict) -> None:
        task_id = payload.get("task_id")
        if task_id:
            self.selected_task_id = task_id
        self._last_task_payload = payload
        self._populate_agent_parent_combo(payload.get("agents", []))
        if self.selected_task_id:
            self._select_task_in_list(self.selected_task_id)
        self._render_task_output(payload)

    def _render_task_output(self, payload: dict) -> None:
        pending = payload.get("pending_approval") or {}
        self._set_task_pending_approval(pending if pending else None)
        suggested_steps = self._suggested_steps_from_task(payload)
        self.task_import_suggestions_button.setEnabled(bool(suggested_steps))
        self._upsert_task_output_tab(payload)

    def _upsert_task_output_tab(self, payload: dict) -> None:
        task_id = str(payload.get("task_id", "")).strip()
        if not task_id:
            self._render_task_output_placeholder(force=True)
            return
        self._clear_task_output_placeholder()
        index = self._find_task_output_tab_index(task_id)
        if index < 0:
            container = QtWidgets.QWidget()
            container.setProperty("task_id", task_id)
            layout = QtWidgets.QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            agent_tabs = QtWidgets.QTabWidget()
            agent_tabs.setObjectName("agentTabs")
            agent_tabs.setDocumentMode(True)
            layout.addWidget(agent_tabs)
            index = self.task_output_tabs.addTab(container, self._task_tab_label(payload))
        else:
            container = self.task_output_tabs.widget(index)
            agent_tabs = container.findChild(QtWidgets.QTabWidget, "agentTabs")
        if agent_tabs is None:
            return
        while agent_tabs.count():
            page = agent_tabs.widget(0)
            agent_tabs.removeTab(0)
            if page is not None:
                page.deleteLater()
        overview = self._create_readonly_text(self._render_task_overview_text(payload))
        agent_tabs.addTab(overview, self._t("task_overview_tab"))
        for full_label, agent in self._flatten_agent_tabs(payload.get("agents", [])):
            page = self._create_readonly_text(
                self._render_agent_text(
                    agent=agent,
                    full_label=full_label,
                    pending=payload.get("pending_approval") or {},
                )
            )
            tab_index = agent_tabs.addTab(page, self._trim_tab_label(full_label))
            agent_tabs.setTabToolTip(tab_index, full_label)
        self.task_output_tabs.setTabText(index, self._task_tab_label(payload))
        self.task_output_tabs.setCurrentIndex(index)

    def _render_task_overview_text(self, payload: dict) -> str:
        lines = [
            f"{self._t('task_label_title')}: {payload.get('title', self._t('task_default_title'))}",
            f"{self._t('task_label_status')}: {payload.get('status', 'draft')}",
            f"{self._t('task_label_task_language')}: {payload.get('preferred_language', SYSTEM_LANGUAGE)}",
            f"{self._t('task_label_agents')}: {self._count_agents(payload.get('agents', []))}",
        ]
        root_agents = payload.get("agents", [])
        if isinstance(root_agents, list) and root_agents:
            root_assignment = self._format_model_assignment(
                root_agents[0].get("model_assignment")
            )
            if root_assignment:
                lines.append(
                    f"{self._t('task_label_agent_model')}: {root_assignment}"
                )
        latest_message = payload.get("last_message") or self._latest_task_message(
            payload.get("results", [])
        )
        if latest_message:
            lines.append(f"{self._t('task_label_latest_message')}: {latest_message}")
        pending = payload.get("pending_approval") or {}
        if pending:
            lines.extend(
                [
                    "",
                    f"{self._t('task_pending_approval')}: {pending.get('agent_name', 'Agent')} step {pending.get('step_index', '?')}",
                    f"{self._t('task_risk')}: {pending.get('risk_level', 'unknown')}",
                    pending.get("summary", ""),
                ]
            )
            warnings = pending.get("warnings", [])
            if isinstance(warnings, list) and warnings:
                lines.extend(str(item) for item in warnings)
        suggested_steps = self._suggested_steps_from_task(payload)
        if suggested_steps:
            lines.append(f"{self._t('task_suggested_steps')}: {len(suggested_steps)}")
        if self.output_mode == OutputMode.FINAL_ONLY.value:
            results = payload.get("results", [])
            if results:
                latest = results[-1]
                lines.extend(
                    [
                        "",
                        f"{self._t('task_heading_results')}:",
                        f"{latest.get('index', 0)}. {self._result_marker(latest)} - {latest.get('message', '')}",
                    ]
                )
            return "\n".join(lines)

        lines.extend(["", f"{self._t('task_heading_steps')}:"])
        for index, step in enumerate(payload.get("steps", []), start=1):
            kind = WorkflowStepType(step.get("kind", WorkflowStepType.CAPTURE_SCREEN.value))
            lines.append(f"{index}. {self._step_label(kind)}{self._step_suffix(step)}")
        agent_lines = self._render_agent_lines(payload.get("agents", []))
        if agent_lines:
            lines.extend(["", f"{self._t('task_heading_agent_tree')}:"] + agent_lines)
        results = payload.get("results", [])
        if results:
            lines.extend(["", f"{self._t('task_heading_results')}:"])
            for result in results:
                lines.append(
                    f"{result.get('index', 0)}. {self._result_marker(result)} - {result.get('message', '')}"
                )
        return "\n".join(lines)

    def _render_agent_text(
        self,
        *,
        agent: dict,
        full_label: str,
        pending: dict,
    ) -> str:
        lines = [
            f"{self._t('task_label_title')}: {full_label}",
            f"{self._t('task_label_agent_status')}: {agent.get('status', 'draft')}",
            f"{self._t('task_label_task_language')}: {agent.get('preferred_language', SYSTEM_LANGUAGE)}",
            f"{self._t('task_label_autonomous')}: {self._bool_label(bool(agent.get('autonomous')))}",
            f"{self._t('task_label_max_iterations')}: {agent.get('max_iterations', 8)}",
        ]
        model_assignment = self._format_model_assignment(agent.get("model_assignment"))
        if model_assignment:
            lines.append(f"{self._t('task_label_agent_model')}: {model_assignment}")
        assignment_reason = None
        assignment = agent.get("model_assignment")
        if isinstance(assignment, dict):
            assignment_reason = assignment.get("assignment_reason")
        if assignment_reason:
            lines.append(
                f"{self._t('task_label_assignment_reason')}: {assignment_reason}"
            )
        parent = agent.get("parent_agent_id")
        if parent:
            lines.append(f"{self._t('task_label_parent')}: {parent}")
        instruction = (agent.get("instruction") or "").strip()
        if instruction:
            lines.append(f"{self._t('task_label_instruction')}: {instruction}")
        last_message = agent.get("last_message")
        if last_message:
            lines.append(f"{self._t('task_label_latest_message')}: {last_message}")
        if pending and pending.get("agent_id") == agent.get("agent_id"):
            lines.extend(
                [
                    "",
                    f"{self._t('task_pending_approval')}: step {pending.get('step_index', '?')}",
                    f"{self._t('task_risk')}: {pending.get('risk_level', 'unknown')}",
                    pending.get("summary", ""),
                ]
            )
        if self.output_mode == OutputMode.FINAL_ONLY.value:
            results = agent.get("results", [])
            if results:
                latest = results[-1]
                lines.extend(
                    [
                        "",
                        f"{self._t('task_heading_results')}:",
                        f"{latest.get('index', 0)}. {self._result_marker(latest)} - {latest.get('message', '')}",
                    ]
                )
            return "\n".join(lines)

        lines.extend(["", f"{self._t('task_heading_steps')}:"])
        for index, step in enumerate(agent.get("steps", []), start=1):
            kind = WorkflowStepType(step.get("kind", WorkflowStepType.CAPTURE_SCREEN.value))
            lines.append(f"{index}. {self._step_label(kind)}{self._step_suffix(step)}")
        results = agent.get("results", [])
        if results:
            lines.extend(["", f"{self._t('task_heading_results')}:"])
            for result in results:
                lines.append(
                    f"{result.get('index', 0)}. {self._result_marker(result)} - {result.get('message', '')}"
                )
                output = result.get("output", {})
                guidance = output.get("guidance") if isinstance(output, dict) else None
                if guidance:
                    lines.append(f"Guidance: {guidance}")
        children = [child.get("name", "Agent") for child in agent.get("children", [])]
        if children:
            lines.extend(["", f"{self._t('task_label_children')}: {', '.join(children)}"])
        return "\n".join(lines)

    def _render_task_output_placeholder(self, *, force: bool = False) -> None:
        real_tabs = [
            self.task_output_tabs.widget(index)
            for index in range(self.task_output_tabs.count())
            if self.task_output_tabs.widget(index) is not None
            and self.task_output_tabs.widget(index).property("task_id")
        ]
        if real_tabs and not force:
            return
        self.task_output_tabs.blockSignals(True)
        while self.task_output_tabs.count():
            page = self.task_output_tabs.widget(0)
            self.task_output_tabs.removeTab(0)
            if page is not None:
                page.deleteLater()
        placeholder = self._create_readonly_text(self._t("task_tabs_empty"))
        placeholder.setProperty("placeholder", True)
        self.task_output_tabs.addTab(placeholder, self._t("task_overview_tab"))
        self.task_output_tabs.blockSignals(False)

    def _clear_task_output_placeholder(self) -> None:
        if self.task_output_tabs.count() != 1:
            return
        widget = self.task_output_tabs.widget(0)
        if widget is None or not widget.property("placeholder"):
            return
        self.task_output_tabs.removeTab(0)
        widget.deleteLater()

    def _find_task_output_tab_index(self, task_id: str) -> int:
        for index in range(self.task_output_tabs.count()):
            widget = self.task_output_tabs.widget(index)
            if widget is not None and widget.property("task_id") == task_id:
                return index
        return -1

    def _select_task_in_list(self, task_id: str) -> None:
        self._suppress_task_selection = True
        for row in range(self.task_list.count()):
            item = self.task_list.item(row)
            if item is not None and item.data(QtCore.Qt.ItemDataRole.UserRole) == task_id:
                self.task_list.setCurrentRow(row)
                break
        self._suppress_task_selection = False

    def _prune_task_output_tabs(self, task_ids: set[str]) -> None:
        indexes_to_remove: list[int] = []
        for index in range(self.task_output_tabs.count()):
            widget = self.task_output_tabs.widget(index)
            if widget is None:
                continue
            task_id = widget.property("task_id")
            if task_id and task_id not in task_ids:
                indexes_to_remove.append(index)
        for index in reversed(indexes_to_remove):
            widget = self.task_output_tabs.widget(index)
            self.task_output_tabs.removeTab(index)
            if widget is not None:
                widget.deleteLater()
        if not task_ids and self.task_output_tabs.count() == 0:
            self._render_task_output_placeholder(force=True)

    def _create_readonly_text(self, text: str) -> QtWidgets.QTextEdit:
        view = QtWidgets.QTextEdit()
        view.setReadOnly(True)
        view.setPlainText(text)
        return view

    def _flatten_agent_tabs(
        self,
        agents: list[dict],
        prefix: list[str] | None = None,
    ) -> list[tuple[str, dict]]:
        labels: list[tuple[str, dict]] = []
        path = prefix or []
        for agent in agents:
            name = str(agent.get("name", "Agent"))
            full_label = " / ".join([*path, name]) if path else name
            labels.append((full_label, agent))
            labels.extend(
                self._flatten_agent_tabs(agent.get("children", []), [*path, name])
            )
        return labels

    def _task_tab_label(self, payload: dict) -> str:
        title = str(payload.get("title", self._t("task_default_title")))
        return self._trim_tab_label(title, max_length=22)

    def _trim_tab_label(self, text: str, *, max_length: int = 18) -> str:
        return text if len(text) <= max_length else text[: max_length - 1] + "…"

    def _set_task_pending_approval(self, pending: dict | None) -> None:
        previous_id = (
            self._task_pending_approval.get("confirmation_id")
            if isinstance(self._task_pending_approval, dict)
            else None
        )
        self._task_pending_approval = pending
        has_pending = bool(pending)
        self.task_approval_group.setVisible(has_pending)
        self.task_approve_button.setEnabled(has_pending)
        self.task_deny_button.setEnabled(has_pending)
        self.task_prompt_button.setEnabled(has_pending)
        if not pending:
            self.task_approval_summary.setText("")
            self.task_approval_timer.setText("")
            self.task_approval_prompt_input.clear()
            return
        if previous_id != pending.get("confirmation_id"):
            self.task_approval_prompt_input.clear()
        lines = [
            pending.get("summary", ""),
            f"{pending.get('agent_name', 'Agent')} · step {pending.get('step_index', '?')} · {pending.get('risk_level', 'unknown')}",
        ]
        warnings = pending.get("warnings", [])
        if isinstance(warnings, list) and warnings:
            lines.extend(str(item) for item in warnings)
        preview = pending.get("preview", "")
        if preview:
            lines.extend(["", preview])
        self.task_approval_summary.setText("\n".join(line for line in lines if line))
        self.task_approval_timer.setText(self._approval_deadline_text(pending))

    def _approval_deadline_text(self, payload: dict) -> str:
        remaining = self._seconds_remaining(payload.get("expires_at"))
        timeout_action = str(
            payload.get("timeout_action", self.approval_timeout_action)
        )
        action_label = self._t(
            {
                ApprovalTimeoutAction.ALLOW.value: "approval_allow",
                ApprovalTimeoutAction.DENY.value: "approval_deny",
                ApprovalTimeoutAction.PROMPT.value: "approval_prompt",
            }.get(timeout_action, "approval_deny")
        )
        return "\n".join(
            [
                self._t("approval_countdown", seconds=max(0, remaining)),
                self._t("approval_timeout_default", action=action_label),
            ]
        )

    def _seconds_remaining(self, expires_at_raw: str | None) -> int:
        if not expires_at_raw:
            return 0
        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
        except ValueError:
            return 0
        remaining = expires_at - datetime.now(timezone.utc)
        return max(0, int(remaining.total_seconds()))

    def _tick_inline_approval_timers(self) -> None:
        if self._task_pending_approval:
            self.task_approval_timer.setText(
                self._approval_deadline_text(self._task_pending_approval)
            )
            if (
                self.selected_task_id
                and self._seconds_remaining(
                    self._task_pending_approval.get("expires_at")
                )
                == 0
            ):
                self._task_pending_approval = None
                self.load_task(self.selected_task_id)
        if self._system_pending_preview:
            self.system_approval_timer.setText(
                self._approval_deadline_text(self._system_pending_preview)
            )
            if (
                self._seconds_remaining(
                    self._system_pending_preview.get("expires_at")
                )
                == 0
            ):
                self._resolve_system_timeout()

    def _on_script_prepared(self, payload: dict) -> None:
        confirmation_id = payload.get("confirmation_id")
        self._pending_script_confirmation_id = confirmation_id
        preview = dict(payload)
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=float(preview.get("approval_timeout_seconds", self.approval_timeout_seconds))
        )
        preview["expires_at"] = expires_at.isoformat()
        preview["timeout_action"] = self.approval_timeout_action
        self._system_pending_preview = preview
        self.system_approval_group.setVisible(True)
        self.system_prompt_input.clear()
        lines = [
            payload.get("summary", ""),
            f"{self._t('script_os_prefix')}: {payload.get('os_name', 'Unknown')}",
            f"{self._t('script_runtime_prefix')}: {payload.get('runtime', 'auto')} / {payload.get('preferred_shell', 'unknown')}",
            f"{self._t('task_risk')}: {payload.get('risk_level', 'unknown')}",
        ]
        warnings = payload.get("warnings", [])
        if isinstance(warnings, list) and warnings:
            lines.extend(["", f"{self._t('script_warning_prefix')}:"])
            lines.extend(str(item) for item in warnings)
        preview_text = payload.get("preview", "")
        if preview_text:
            lines.extend(["", f"{self._t('script_preview_prefix')}: ", preview_text])
        self.system_approval_summary.setText("\n".join(lines))
        self.system_approval_timer.setText(self._approval_deadline_text(preview))
        self.system_script_output.setPlainText("\n".join(lines))
        self.statusBar().showMessage(self._t("status_script_review_ready"), 3000)

    def approve_system_script(self) -> None:
        self._run_prepared_system_script(auto=False)

    def deny_system_script(self) -> None:
        self._clear_system_pending_preview()
        self.statusBar().showMessage(self._t("status_script_denied"), 3000)

    def prompt_system_script(self) -> None:
        guidance = self.system_prompt_input.toPlainText().strip()
        if not guidance:
            self.statusBar().showMessage(self._t("status_script_prompt_required"), 3000)
            return
        self.system_script_output.setPlainText(
            "\n".join(
                [
                    self._t("status_script_prompt_recorded"),
                    "",
                    guidance,
                ]
            )
        )
        self._clear_system_pending_preview()
        self.statusBar().showMessage(self._t("status_script_prompt_recorded"), 3000)

    def _resolve_system_timeout(self) -> None:
        action = ApprovalTimeoutAction(self.approval_timeout_action)
        if action == ApprovalTimeoutAction.ALLOW:
            self._run_prepared_system_script(auto=True)
            self.statusBar().showMessage(self._t("status_script_auto_allowed"), 3000)
            return
        if action == ApprovalTimeoutAction.PROMPT:
            self.system_script_output.setPlainText(
                "\n".join(
                    [
                        self._t("status_script_auto_prompt"),
                        "",
                        self.approval_timeout_prompt,
                    ]
                )
            )
            self._clear_system_pending_preview()
            self.statusBar().showMessage(self._t("status_script_auto_prompt"), 3000)
            return
        self._clear_system_pending_preview()
        self.statusBar().showMessage(self._t("status_script_auto_denied"), 3000)

    def _run_prepared_system_script(self, *, auto: bool) -> None:
        confirmation_id = self._pending_script_confirmation_id
        if not confirmation_id:
            return
        self._clear_system_pending_preview()
        self._queue_request(
            path="/api/system/script/execute",
            method="POST",
            payload={
                "confirmation_id": confirmation_id,
                "confirm": True,
            },
            on_success=self._on_script_executed,
            error_message="Script execution failed",
        )

    def _clear_system_pending_preview(self) -> None:
        self._system_pending_preview = None
        self._pending_script_confirmation_id = None
        self.system_approval_group.setVisible(False)
        self.system_approval_summary.setText(self._t("system_inline_review_placeholder"))
        self.system_approval_timer.setText("")
        self.system_prompt_input.clear()

    def _on_script_executed(self, payload: dict) -> None:
        self._clear_system_pending_preview()
        stdout = payload.get("stdout", "")
        stderr = payload.get("stderr", "")
        sections = [
            payload.get("summary", ""),
            f"{self._t('script_runtime_prefix')}: {payload.get('runtime', 'auto')} / {payload.get('preferred_shell', 'unknown')}",
            f"Exit Code: {payload.get('exit_code', 'n/a')}",
        ]
        if payload.get("timed_out"):
            sections.append("Timed Out: True")
        if stdout:
            sections.extend(["", "STDOUT:", stdout])
        if stderr:
            sections.extend(["", "STDERR:", stderr])
        self.system_script_output.setPlainText("\n".join(sections))
        self.statusBar().showMessage(self._t("status_script_running"), 4000)
