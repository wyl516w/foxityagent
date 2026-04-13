from __future__ import annotations

from PySide6 import QtWidgets

from agent_studio.core.models import (
    ApprovalTimeoutAction,
    ControlMode,
    OutputMode,
    ProviderType,
)
from agent_studio.ui.i18n import LANGUAGE_PRESETS


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, translate_fn, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._t = translate_fn
        self._build_ui()
        self.retranslate()

    def _build_ui(self) -> None:
        self.resize(760, 520)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs, stretch=1)

        self.provider_tab = QtWidgets.QWidget()
        provider_layout = QtWidgets.QGridLayout(self.provider_tab)
        self.provider_label = QtWidgets.QLabel()
        self.provider_combo = QtWidgets.QComboBox()
        self.provider_combo.addItem("", ProviderType.MOCK.value)
        self.provider_combo.addItem("", ProviderType.OPENAI_COMPATIBLE.value)
        self.provider_combo.addItem("", ProviderType.OLLAMA.value)
        self.model_label = QtWidgets.QLabel()
        self.model_input = QtWidgets.QLineEdit()
        self.base_url_label = QtWidgets.QLabel()
        self.base_url_input = QtWidgets.QLineEdit()
        self.api_key_label = QtWidgets.QLabel()
        self.api_key_input = QtWidgets.QLineEdit()
        self.api_key_input.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.org_label = QtWidgets.QLabel()
        self.org_input = QtWidgets.QLineEdit()
        self.connectivity_label = QtWidgets.QLabel()
        self.provider_health_label = QtWidgets.QLabel()
        self.provider_health_label.setProperty("muted", True)
        self.connectivity_report_label = QtWidgets.QLabel()
        self.connectivity_output = QtWidgets.QTextEdit()
        self.connectivity_output.setReadOnly(True)
        self.connectivity_output.setMinimumHeight(110)
        self.allow_mock_fallback_checkbox = QtWidgets.QCheckBox()
        self.capabilities_label = QtWidgets.QLabel()
        self.capabilities_output = QtWidgets.QTextEdit()
        self.capabilities_output.setReadOnly(True)
        self.capabilities_output.setMinimumHeight(120)
        self.test_button = QtWidgets.QPushButton()
        self.test_all_button = QtWidgets.QPushButton()

        provider_layout.addWidget(self.provider_label, 0, 0)
        provider_layout.addWidget(self.provider_combo, 0, 1)
        provider_layout.addWidget(self.model_label, 1, 0)
        provider_layout.addWidget(self.model_input, 1, 1)
        provider_layout.addWidget(self.base_url_label, 2, 0)
        provider_layout.addWidget(self.base_url_input, 2, 1)
        provider_layout.addWidget(self.api_key_label, 3, 0)
        provider_layout.addWidget(self.api_key_input, 3, 1)
        provider_layout.addWidget(self.org_label, 4, 0)
        provider_layout.addWidget(self.org_input, 4, 1)
        provider_layout.addWidget(self.connectivity_label, 5, 0)
        provider_layout.addWidget(self.provider_health_label, 5, 1)
        provider_layout.addWidget(self.connectivity_report_label, 6, 0)
        provider_layout.addWidget(self.connectivity_output, 6, 1)
        provider_layout.addWidget(self.allow_mock_fallback_checkbox, 7, 0, 1, 2)
        provider_layout.addWidget(self.capabilities_label, 8, 0)
        provider_layout.addWidget(self.capabilities_output, 8, 1)
        provider_buttons = QtWidgets.QHBoxLayout()
        provider_buttons.addStretch(1)
        provider_buttons.addWidget(self.test_all_button)
        provider_buttons.addWidget(self.test_button)
        provider_layout.addLayout(provider_buttons, 9, 1)
        provider_layout.setColumnStretch(1, 1)
        self.tabs.addTab(self.provider_tab, "")

        self.control_tab = QtWidgets.QWidget()
        control_layout = QtWidgets.QFormLayout(self.control_tab)
        self.control_mode_combo = QtWidgets.QComboBox()
        self.control_mode_combo.addItem("", ControlMode.DENY.value)
        self.control_mode_combo.addItem("", ControlMode.ASK.value)
        self.control_mode_combo.addItem("", ControlMode.ALLOW_SESSION.value)
        self.control_mode_combo.addItem("", ControlMode.ALLOW_ALWAYS.value)
        self.control_mode_label = QtWidgets.QLabel()
        self.approval_timeout_label = QtWidgets.QLabel()
        self.approval_timeout_spin = QtWidgets.QSpinBox()
        self.approval_timeout_spin.setRange(5, 600)
        self.approval_timeout_spin.setValue(60)
        self.approval_timeout_action_label = QtWidgets.QLabel()
        self.approval_timeout_action_combo = QtWidgets.QComboBox()
        self.approval_timeout_action_combo.addItem("", ApprovalTimeoutAction.ALLOW.value)
        self.approval_timeout_action_combo.addItem("", ApprovalTimeoutAction.DENY.value)
        self.approval_timeout_action_combo.addItem("", ApprovalTimeoutAction.PROMPT.value)
        self.approval_timeout_prompt_label = QtWidgets.QLabel()
        self.approval_timeout_prompt_input = QtWidgets.QPlainTextEdit()
        self.approval_timeout_prompt_input.setFixedHeight(90)
        control_layout.addRow(self.control_mode_label, self.control_mode_combo)
        control_layout.addRow(self.approval_timeout_label, self.approval_timeout_spin)
        control_layout.addRow(
            self.approval_timeout_action_label,
            self.approval_timeout_action_combo,
        )
        control_layout.addRow(
            self.approval_timeout_prompt_label,
            self.approval_timeout_prompt_input,
        )
        self.tabs.addTab(self.control_tab, "")

        self.interface_tab = QtWidgets.QWidget()
        interface_layout = QtWidgets.QFormLayout(self.interface_tab)
        self.language_label = QtWidgets.QLabel()
        self.language_combo = QtWidgets.QComboBox()
        self.language_combo.setEditable(True)
        for code, label in LANGUAGE_PRESETS:
            self.language_combo.addItem(label, code)
        self.output_mode_label = QtWidgets.QLabel()
        self.output_mode_combo = QtWidgets.QComboBox()
        self.output_mode_combo.addItem("", OutputMode.FINAL_ONLY.value)
        self.output_mode_combo.addItem("", OutputMode.STEP_SUMMARY.value)
        self.output_mode_help = QtWidgets.QLabel()
        self.output_mode_help.setWordWrap(True)
        self.output_mode_help.setProperty("muted", True)
        self.language_help = QtWidgets.QLabel()
        self.language_help.setWordWrap(True)
        self.language_help.setProperty("muted", True)
        interface_layout.addRow(self.language_label, self.language_combo)
        interface_layout.addRow(self.output_mode_label, self.output_mode_combo)
        interface_layout.addRow(QtWidgets.QLabel(""), self.output_mode_help)
        interface_layout.addRow(QtWidgets.QLabel(""), self.language_help)
        self.tabs.addTab(self.interface_tab, "")

        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch(1)
        self.cancel_button = QtWidgets.QPushButton()
        self.save_button = QtWidgets.QPushButton()
        self.cancel_button.clicked.connect(self.reject)
        self.save_button.clicked.connect(self.accept)
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.save_button)
        layout.addLayout(button_row)

    def retranslate(self) -> None:
        self.setWindowTitle(self._t("settings_dialog_title"))
        self.tabs.setTabText(0, self._t("settings_provider"))
        self.tabs.setTabText(1, self._t("settings_control"))
        self.tabs.setTabText(2, self._t("settings_ui"))
        self.provider_label.setText(self._t("provider"))
        self.model_label.setText(self._t("settings_model"))
        self.base_url_label.setText(self._t("settings_base_url"))
        self.api_key_label.setText(self._t("settings_api_key"))
        self.org_label.setText(self._t("settings_organization"))
        self.connectivity_label.setText(self._t("settings_connectivity"))
        self.provider_health_label.setText(self._t("settings_not_tested"))
        self.connectivity_report_label.setText(self._t("settings_connectivity_report"))
        self.allow_mock_fallback_checkbox.setText(self._t("settings_allow_mock_fallback"))
        self.capabilities_label.setText(self._t("settings_capabilities"))
        self.test_button.setText(self._t("settings_test"))
        self.test_all_button.setText(self._t("settings_test_all"))
        self.control_mode_label.setText(self._t("control_mode"))
        self.approval_timeout_label.setText(self._t("settings_approval_timeout"))
        self.approval_timeout_action_label.setText(
            self._t("settings_approval_timeout_action")
        )
        self.approval_timeout_prompt_label.setText(
            self._t("settings_approval_timeout_prompt")
        )
        self.language_label.setText(self._t("language"))
        self.output_mode_label.setText(self._t("settings_output_mode"))
        self.output_mode_combo.setItemText(0, self._t("output_final_only"))
        self.output_mode_combo.setItemText(1, self._t("output_step_summary"))
        self.output_mode_help.setText(self._t("settings_output_mode_help"))
        self.language_help.setText(self._t("settings_language_help"))
        self.cancel_button.setText(self._t("settings_cancel"))
        self.save_button.setText(self._t("settings_save"))
        self.provider_combo.setItemText(0, self._t("provider_mock"))
        self.provider_combo.setItemText(1, self._t("provider_openai_compatible"))
        self.provider_combo.setItemText(2, self._t("provider_ollama"))
        self.control_mode_combo.setItemText(0, self._t("control_deny"))
        self.control_mode_combo.setItemText(1, self._t("control_ask"))
        self.control_mode_combo.setItemText(2, self._t("control_allow_session"))
        self.control_mode_combo.setItemText(3, self._t("control_allow_always"))
        self.approval_timeout_action_combo.setItemText(
            0,
            self._t("approval_allow"),
        )
        self.approval_timeout_action_combo.setItemText(
            1,
            self._t("approval_deny"),
        )
        self.approval_timeout_action_combo.setItemText(
            2,
            self._t("approval_prompt"),
        )
        for index, (_, label) in enumerate(LANGUAGE_PRESETS):
            self.language_combo.setItemText(index, label)

    def set_snapshot(self, payload: dict) -> None:
        provider = payload.get("provider", {})
        automation = payload.get("automation", {})
        ui_state = payload.get("ui", {})
        self.provider_combo.setCurrentIndex(
            max(0, self.provider_combo.findData(provider.get("provider", ProviderType.MOCK.value)))
        )
        self.model_input.setText(provider.get("model", ""))
        self.base_url_input.setText(provider.get("base_url", ""))
        self.api_key_input.setText(provider.get("api_key", ""))
        self.org_input.setText(provider.get("organization") or "")
        self.allow_mock_fallback_checkbox.setChecked(
            provider.get("allow_mock_fallback", True)
        )
        self.control_mode_combo.setCurrentIndex(
            max(
                0,
                self.control_mode_combo.findData(
                    automation.get("control_mode", ControlMode.ASK.value)
                ),
            )
        )
        self.approval_timeout_spin.setValue(
            int(automation.get("approval_timeout_seconds", 60))
        )
        self.approval_timeout_action_combo.setCurrentIndex(
            max(
                0,
                self.approval_timeout_action_combo.findData(
                    automation.get(
                        "approval_timeout_action",
                        ApprovalTimeoutAction.DENY.value,
                    )
                ),
            )
        )
        self.approval_timeout_prompt_input.setPlainText(
            automation.get("approval_timeout_prompt", "")
        )
        language_value = ui_state.get("language", "system")
        combo_index = self.language_combo.findData(language_value)
        if combo_index >= 0:
            self.language_combo.setCurrentIndex(combo_index)
        else:
            self.language_combo.setEditText(language_value)
        self.output_mode_combo.setCurrentIndex(
            max(
                0,
                self.output_mode_combo.findData(
                    ui_state.get("output_mode", OutputMode.FINAL_ONLY.value)
                ),
            )
        )
        self.capabilities_output.setPlainText(self._t("settings_capabilities_loading"))
        self.connectivity_output.setPlainText(
            self._t("settings_connectivity_report_placeholder")
        )

    def build_provider_payload(self, timeout_seconds: float) -> dict:
        return {
            "provider": self.provider_combo.currentData(),
            "base_url": self.base_url_input.text().strip(),
            "api_key": self.api_key_input.text().strip(),
            "model": self.model_input.text().strip(),
            "organization": self.org_input.text().strip() or None,
            "timeout_seconds": timeout_seconds,
            "allow_mock_fallback": self.allow_mock_fallback_checkbox.isChecked(),
        }

    def build_apply_payload(self, timeout_seconds: float) -> dict:
        return {
            "provider": self.build_provider_payload(timeout_seconds),
            "automation": {
                "control_mode": self.control_mode_combo.currentData(),
                "approval_timeout_seconds": self.approval_timeout_spin.value(),
                "approval_timeout_action": self.approval_timeout_action_combo.currentData(),
                "approval_timeout_prompt": (
                    self.approval_timeout_prompt_input.toPlainText().strip()
                ),
            },
            "ui": {
                "language": self.current_language_value(),
                "output_mode": self.current_output_mode_value(),
            },
        }

    def current_language_value(self) -> str:
        return self.language_combo.currentData() or self.language_combo.currentText().strip() or "system"

    def current_output_mode_value(self) -> str:
        return self.output_mode_combo.currentData() or OutputMode.FINAL_ONLY.value

    def set_health_result(self, text: str) -> None:
        self.provider_health_label.setText(text)

    def set_capabilities_result(self, text: str) -> None:
        self.capabilities_output.setPlainText(text)

    def set_connectivity_report(self, text: str) -> None:
        self.connectivity_output.setPlainText(text)
