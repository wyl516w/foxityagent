from __future__ import annotations

from typing import Any
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class ProviderType(str, Enum):
    MOCK = "mock"
    OPENAI_COMPATIBLE = "openai_compatible"
    OLLAMA = "ollama"


class ControlMode(str, Enum):
    DENY = "deny"
    ASK = "ask"
    ALLOW_SESSION = "allow_session"
    ALLOW_ALWAYS = "allow_always"


class ApprovalTimeoutAction(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    PROMPT = "prompt"


class ProviderSettingsPayload(BaseModel):
    provider: ProviderType = ProviderType.MOCK
    base_url: str = ""
    api_key: str = ""
    model: str = "gpt-4.1-mini"
    organization: str | None = None
    timeout_seconds: float = 60.0
    allow_mock_fallback: bool = True


class AutomationSettingsPayload(BaseModel):
    control_mode: ControlMode = ControlMode.ASK
    approval_timeout_seconds: int = 60
    approval_timeout_action: ApprovalTimeoutAction = ApprovalTimeoutAction.DENY
    approval_timeout_prompt: str = (
        "Approval timed out. Continue with a safer alternative and avoid the blocked high-risk action."
    )
    script_review_settings: ProviderSettingsPayload = Field(
        default_factory=lambda: ProviderSettingsPayload(
            provider=ProviderType.OPENAI_COMPATIBLE,
            base_url="",
            api_key="",
            model="gpt-4.1",
            organization=None,
            timeout_seconds=60.0,
            allow_mock_fallback=False,
        )
    )


class OutputMode(str, Enum):
    FINAL_ONLY = "final_only"
    STEP_SUMMARY = "step_summary"


class UiStatePayload(BaseModel):
    current_conversation_id: str | None = None
    latest_capture_path: str | None = None
    language: str = "system"
    output_mode: OutputMode = OutputMode.FINAL_ONLY


class ChatImageAttachment(BaseModel):
    name: str | None = None
    media_type: str = "image/png"
    image_path: str | None = None
    image_base64: str | None = None

    @model_validator(mode="after")
    def validate_source(self) -> "ChatImageAttachment":
        if self.image_path or self.image_base64 or self.name:
            return self
        raise ValueError(
            "Chat image attachments require image_path, image_base64, or a stored name."
        )


class ChatRequest(BaseModel):
    message: str = Field(default="", max_length=8000)
    conversation_id: str | None = None
    attachments: list[ChatImageAttachment] = Field(default_factory=list)
    system_prompt: str = "You are a helpful desktop automation copilot."

    @model_validator(mode="after")
    def validate_message_or_attachment(self) -> "ChatRequest":
        if self.message.strip() or self.attachments:
            return self
        raise ValueError("Chat requests require a message or at least one image attachment.")


class ChatResponse(BaseModel):
    provider: ProviderType
    model: str
    content: str
    conversation_id: str | None = None
    task_id: str | None = None
    task_status: str | None = None
    task_title: str | None = None
    used_mock: bool = False
    vision_used: bool = False
    attachment_count: int = 0
    fallback_used: bool = False
    fallback_reason: str | None = None
    attempted_providers: list[ProviderType] = Field(default_factory=list)
    latency_ms: int = 0


class ProviderHealthResponse(BaseModel):
    provider: ProviderType
    base_url: str
    model: str
    selected_model_available: bool = True
    ok: bool
    reachable: bool
    authenticated: bool
    message: str
    latency_ms: int = 0
    discovered_models: list[str] = Field(default_factory=list)


class ProviderCapabilityProfile(BaseModel):
    provider: ProviderType
    label: str
    supports_text: bool = True
    supports_vision: bool = False
    supports_tools: bool = False
    supports_model_listing: bool = False
    local_runtime: bool = False
    remote_runtime: bool = False
    default_model: str
    routing_hint: str


class ProviderCapabilitiesResponse(BaseModel):
    current_provider: ProviderType
    current_model: str
    allow_mock_fallback: bool = True
    capabilities: list[ProviderCapabilityProfile] = Field(default_factory=list)


class ProviderHealthSweepResponse(BaseModel):
    current_provider: ProviderType
    current_model: str
    ok_count: int = 0
    reachable_count: int = 0
    results: list[ProviderHealthResponse] = Field(default_factory=list)


class PermissionDecisionPayload(BaseModel):
    allowed: bool
    requires_confirmation: bool
    mode: ControlMode
    message: str


class ScriptRuntime(str, Enum):
    AUTO = "auto"
    PYTHON = "python"
    SHELL = "shell"


class ScriptRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ControlActionType(str, Enum):
    MOVE_MOUSE = "move_mouse"
    LEFT_CLICK = "left_click"
    TYPE_TEXT = "type_text"


class ControlActionPayload(BaseModel):
    action: ControlActionType
    text: str | None = None


class ControlActionResult(BaseModel):
    allowed: bool
    executed: bool
    message: str
    event: str


class ScreenshotResponse(BaseModel):
    ok: bool
    image_path: str | None = None
    width: int = 0
    height: int = 0
    message: str


class OcrTextLine(BaseModel):
    text: str
    score: float = 0.0
    bbox: list[list[int]] = Field(default_factory=list)


class OcrRequest(BaseModel):
    image_path: str | None = None


class OcrResponse(BaseModel):
    ok: bool
    image_path: str | None = None
    engine: str
    lines: list[OcrTextLine] = Field(default_factory=list)
    message: str


class ElementLookupRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    image_path: str | None = None
    case_sensitive: bool = False


class ElementMatch(BaseModel):
    text: str
    score: float = 0.0
    bbox: list[list[int]] = Field(default_factory=list)
    center_x: int
    center_y: int


class ElementLookupResponse(BaseModel):
    ok: bool
    image_path: str | None = None
    query: str
    matches: list[ElementMatch] = Field(default_factory=list)
    message: str


class SystemInfoResponse(BaseModel):
    os_name: str
    os_release: str
    os_version: str
    machine: str
    python_version: str
    preferred_script_runtime: ScriptRuntime
    preferred_shell: str
    screenshot_backend: str
    ocr_backend: str


class ScriptExecutionPrepareRequest(BaseModel):
    script: str = Field(min_length=1, max_length=40000)
    runtime: ScriptRuntime = ScriptRuntime.AUTO
    timeout_seconds: float = 30.0
    approval_timeout_seconds: float | None = None


class ScriptExecutionPreviewResponse(BaseModel):
    confirmation_id: str
    runtime: ScriptRuntime
    preferred_shell: str
    risk_level: ScriptRiskLevel
    review_provider: ProviderType | None = None
    review_model: str | None = None
    review_summary: str | None = None
    requires_confirmation: bool = True
    approval_timeout_seconds: float = 60.0
    summary: str
    warnings: list[str] = Field(default_factory=list)
    preview: str
    os_name: str


class ScriptExecutionRunRequest(BaseModel):
    confirmation_id: str = Field(min_length=1, max_length=200)
    confirm: bool = False


class ScriptExecutionResponse(BaseModel):
    ok: bool
    confirmation_id: str
    runtime: ScriptRuntime
    preferred_shell: str
    exit_code: int | None = None
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""
    summary: str


class SettingsSnapshot(BaseModel):
    provider: ProviderSettingsPayload
    automation: AutomationSettingsPayload
    ui: UiStatePayload
    recent_events: list[str]


class AppSettingsUpdateRequest(BaseModel):
    provider: ProviderSettingsPayload | None = None
    automation: AutomationSettingsPayload | None = None
    ui: UiStatePayload | None = None


class ConversationSummary(BaseModel):
    conversation_id: str
    title: str
    message_count: int = 0
    created_at: str
    updated_at: str


class ConversationMessage(BaseModel):
    message_id: str
    role: str
    content: str
    created_at: str
    attachments: list[ChatImageAttachment] = Field(default_factory=list)
    linked_task_id: str | None = None


class ConversationListResponse(BaseModel):
    conversations: list[ConversationSummary]


class ConversationHistoryResponse(BaseModel):
    conversation: ConversationSummary
    messages: list[ConversationMessage]


class CreateConversationRequest(BaseModel):
    title: str | None = None


class TaskStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowStepType(str, Enum):
    DETECT_SYSTEM = "detect_system"
    CAPTURE_SCREEN = "capture_screen"
    RUN_OCR = "run_ocr"
    FIND_TEXT = "find_text"
    ANALYZE_IMAGE = "analyze_image"
    EXECUTE_SCRIPT = "execute_script"
    MOVE_MOUSE = "move_mouse"
    LEFT_CLICK = "left_click"
    TYPE_TEXT = "type_text"
    DELEGATE_AGENT = "delegate_agent"
    COMPLETE = "complete"


class WorkflowStepDefinition(BaseModel):
    kind: WorkflowStepType
    label: str | None = None
    text: str | None = None
    image_path: str | None = None
    runtime: ScriptRuntime | None = None
    case_sensitive: bool = False
    continue_on_error: bool = False


class WorkflowStepResult(BaseModel):
    index: int
    kind: WorkflowStepType
    agent_id: str | None = None
    agent_name: str | None = None
    label: str | None = None
    ok: bool
    message: str
    output: dict[str, Any] = Field(default_factory=dict)


class AgentModelAssignment(BaseModel):
    provider: ProviderType | None = None
    model: str | None = None
    base_url: str | None = None
    assignment_reason: str | None = None

    @model_validator(mode="after")
    def validate_assignment(self) -> "AgentModelAssignment":
        self.model = (self.model or "").strip() or None
        self.base_url = (self.base_url or "").strip() or None
        self.assignment_reason = (self.assignment_reason or "").strip() or None
        if self.provider is None and self.model is None and self.base_url is None:
            raise ValueError(
                "Agent model assignments require a provider, model, or base_url override."
            )
        return self


class WorkflowAgentNode(BaseModel):
    agent_id: str
    parent_agent_id: str | None = None
    name: str
    instruction: str = ""
    preferred_language: str = "system"
    model_assignment: AgentModelAssignment | None = None
    autonomous: bool = False
    max_iterations: int = 8
    status: TaskStatus = TaskStatus.DRAFT
    steps: list[WorkflowStepDefinition] = Field(default_factory=list)
    results: list[WorkflowStepResult] = Field(default_factory=list)
    last_message: str | None = None
    children: list["WorkflowAgentNode"] = Field(default_factory=list)


class CreateWorkflowTaskRequest(BaseModel):
    title: str | None = None
    conversation_id: str | None = None
    instruction: str = ""
    source_message_id: str | None = None
    source_message_preview: str | None = None
    model_assignment: AgentModelAssignment | None = None
    autonomous: bool = False
    max_iterations: int = Field(default=8, ge=1, le=24)
    preferred_language: str = "system"
    steps: list[WorkflowStepDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_instruction_or_steps(self) -> "CreateWorkflowTaskRequest":
        if self.steps:
            return self
        if self.instruction.strip() and self.autonomous:
            return self
        raise ValueError(
            "Workflow tasks require seed steps or an autonomous instruction with autonomous mode enabled."
        )


class CreateTaskAgentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_agent_id: str | None = None
    instruction: str = ""
    model_assignment: AgentModelAssignment | None = None
    autonomous: bool = False
    max_iterations: int = Field(default=8, ge=1, le=24)
    preferred_language: str = "system"
    steps: list[WorkflowStepDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_instruction_or_steps(self) -> "CreateTaskAgentRequest":
        if self.steps:
            return self
        if self.instruction.strip() and self.autonomous:
            return self
        raise ValueError(
            "Task agents require seed steps or an autonomous instruction with autonomous mode enabled."
        )


class WorkflowTaskSummary(BaseModel):
    task_id: str
    title: str
    status: TaskStatus
    conversation_id: str | None = None
    source_message_id: str | None = None
    source_message_preview: str | None = None
    step_count: int
    agent_count: int = 0
    preferred_language: str = "system"
    last_message: str | None = None
    created_at: str
    updated_at: str


class WorkflowTaskDetail(BaseModel):
    task_id: str
    title: str
    status: TaskStatus
    conversation_id: str | None = None
    source_message_id: str | None = None
    source_message_preview: str | None = None
    preferred_language: str = "system"
    steps: list[WorkflowStepDefinition] = Field(default_factory=list)
    results: list[WorkflowStepResult] = Field(default_factory=list)
    agents: list[WorkflowAgentNode] = Field(default_factory=list)
    created_at: str
    updated_at: str
    last_message: str | None = None
    pending_approval: dict[str, Any] | None = None


class WorkflowTaskListResponse(BaseModel):
    tasks: list[WorkflowTaskSummary]


class WorkflowTaskDetailListResponse(BaseModel):
    tasks: list[WorkflowTaskDetail]


class WorkflowAgentTreeResponse(BaseModel):
    task_id: str
    title: str
    agents: list[WorkflowAgentNode] = Field(default_factory=list)


class WorkflowRunResponse(BaseModel):
    task: WorkflowTaskDetail


class WorkflowApprovalDecisionRequest(BaseModel):
    decision: ApprovalTimeoutAction = ApprovalTimeoutAction.ALLOW
    extra_prompt: str | None = None


class HealthResponse(BaseModel):
    status: str = "ok"
    app_name: str
    backend_url: str
    provider: ProviderType
    control_mode: ControlMode
    input_controller: str
    event_count: int
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


WorkflowAgentNode.model_rebuild()
