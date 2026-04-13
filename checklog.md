# Check Log

## 2026-04-13

- Entered phase G / step 1 and rewrote `实现计划.md` to formally shift the project from a workflow-first prototype toward a conversation-driven desktop agent workbench.
- Added two explicit planning tracks to `实现计划.md`: one for immediate code-governance cleanup (`main_window.py` duplicates, API/UI contract mismatches, backend fail-fast, audit persistence, query efficiency, test drift) and one for the product-level transition to a conversation-centered agent loop.
- Replaced `用户手册.md` with a clean current-state manual that reflects the new product direction without claiming the conversation-first UI refactor is already complete.
- This round is documentation-only; the next implementation step is phase G / step 2: simplify the main window and remove workflow-first UI residue.

- Entered phase F / step 7 and wired the chat composer into a one-click local autonomous-task launcher so a single goal can create and immediately run a task without manually switching to the task builder first.
- Updated `src/agent_studio/ui/main_window.py` so the prompt area now offers both normal chat sending and `Start Local Agent Task`, with the local-task path automatically creating a root agent bound to `Ollama / qwen3-vl:4b`, seeding attached images as `analyze_image` steps, opening the task tab, and starting execution right away.
- Updated `src/agent_studio/services/workflows/workflow_service.py` so image-based workflow steps keep their image path in the active execution context, allowing chat-seeded visual tasks to continue planning against the same image after the first analysis step.
- Updated `src/agent_studio/ui/i18n.py` with prompt-bar labels and status messages for the new local autonomous-task launcher.
- Updated `用户手册.md` and `实现计划.md` to document the new chat-to-task flow, the default local-model behavior, and the latest stage status.
- Verified this round with `.venv\\Scripts\\python.exe -m py_compile`, `pytest -q tests/test_provider_health.py tests/test_model_router_fallback.py tests/test_workflow_routes.py` (`13 passed`), and `.venv\\Scripts\\python.exe -m ruff check src tests`.

- Entered phase F / step 6 and added multi-model agent routing so autonomous parent agents can assign a different provider/model/base URL to each delegated subagent instead of forcing one shared model for the entire tree.
- Extended `src/agent_studio/core/models.py` with `AgentModelAssignment`, provider-health sweep responses, and per-agent `model_assignment` fields for workflow tasks, agent creation, and persisted agent trees.
- Updated `src/agent_studio/services/model_router.py` so chat calls can run with agent-level settings overrides, provider health can be checked across all configured routes in one call, and route resolution can inherit global credentials while swapping to local or alternate models.
- Updated `src/agent_studio/services/workflows/workflow_service.py` so autonomous planning includes current route context, delegated subagents can carry provider/model assignments plus assignment reasons, and `analyze_image` uses the active agent's own model route.
- Updated `src/agent_studio/api/routes.py` with `/api/provider/health/all` so the desktop UI and external API callers can verify all configured provider routes, including local Ollama, through a single endpoint.
- Updated `src/agent_studio/ui/settings_dialog.py`, `src/agent_studio/ui/main_window.py`, and `src/agent_studio/ui/i18n.py` so settings now support `Test All Providers`, show per-route connectivity/model availability details, and display agent-specific model routing in the task/agent tabs.
- Expanded `tests/test_provider_health.py`, `tests/test_model_router_fallback.py`, and `tests/test_workflow_routes.py` to cover full provider sweeps, agent-level route resolution, and delegated child-agent model assignment.
- Updated `用户手册.md` and `实现计划.md` to document full provider connectivity checks, child-agent model assignment, the new API contract, and the updated implementation-stage status.
- Verified this round with `.venv\\Scripts\\python.exe -m py_compile`, `pytest -q tests/test_provider_health.py tests/test_model_router_fallback.py tests/test_workflow_routes.py` (`13 passed`), and `.venv\\Scripts\\python.exe -m ruff check src tests`.

- Entered phase F / step 5 and added autonomous workflow execution so tasks and agents no longer require humans to predefine every step before running.
- Extended `src/agent_studio/core/models.py` with autonomous-task and autonomous-agent request fields (`instruction`, `autonomous`, `max_iterations`) plus internal result kinds for `delegate_agent` and `complete`.
- Reworked `src/agent_studio/services/workflows/workflow_service.py` into a dual-mode executor: it still runs seed steps when provided, but can now ask the active model for the next action, append that action into the task plan, execute it, and repeat until completion or the max planning-turn limit is reached.
- Added autonomous delegation support in `src/agent_studio/services/workflows/workflow_service.py`, allowing any autonomous agent to create its own subagent during execution and continue recursively.
- Updated `src/agent_studio/ui/main_window.py` so the task builder now supports `Goal / Instruction`, `Autonomous Agent`, and `Max Planning Turns`, letting users create title-only or instruction-only autonomous tasks without hand-building a full step list first.
- Updated `src/agent_studio/ui/i18n.py` with strings for autonomous-task creation, instruction entry, and autonomous-agent status labels in the task/agent tabs.
- Updated `tests/test_workflow_routes.py` to cover the new approval payload contract and to verify that an autonomous task can run with no seed steps and delegate work to a subagent.
- Replaced `用户手册.md` with a refreshed manual that documents autonomous tasks, optional seed steps, model-driven subagent delegation, and the updated API contract.
- Verified this round with a targeted `.venv\\Scripts\\python.exe` `py_compile` pass and `pytest -q tests/test_workflow_routes.py` (`4 passed`).

## 2026-04-11

- Entered phase A / step 1 and added SQLite persistence configuration in `src/agent_studio/core/config.py` with database path and event retention settings.
- Added `src/agent_studio/storage/sqlite_store.py` and `src/agent_studio/storage/__init__.py` to create the SQLite schema and provide persisted settings and event access.
- Wired the SQLite store into `src/agent_studio/app.py` so the database initializes during startup.
- Updated `src/agent_studio/core/state.py` to load persisted settings and events and save provider, automation, and event changes back to SQLite.
- Extended `.env.example` with database and event retention environment variables.
- Added `tests/test_sqlite_persistence.py` to verify schema creation and persisted state reloading through SQLite.
- Updated `pyproject.toml` and `.gitignore` so pytest uses a workspace-local temp directory instead of a restricted system temp path.
- Reworked `tests/test_sqlite_persistence.py` to use project-local test directories and adjusted `pyproject.toml` and `.gitignore` accordingly, avoiding flaky temp-directory permissions.
- Verified the SQLite persistence layer with `pytest` and a manual smoke reload against a real `.db` file in `data/`.
- Removed the temporary smoke-test database artifact `data/test_smoke.db` after verification.
- Entered phase A / step 2 and added conversation and UI-state models, SQLite methods, and `src/agent_studio/services/conversation_service.py` to support persisted chat history.
- Extended the FastAPI layer with conversation list, detail, create routes, and UI-state settings persistence.
- Reworked `src/agent_studio/ui/main_window.py` to show saved conversations, switch history, create new conversations, and keep the selected conversation in sync.
- Added API and persistence tests for conversation history plus persisted UI state.
- Verified the conversation flow with `pytest` and a real backend smoke run, then removed the temporary smoke database artifact `data/test_step2_smoke.db`.
- Entered phase A / step 3 and added provider health-check models and provider-level connectivity detection for mock, OpenAI-compatible, and Ollama backends.
- Added a provider test API endpoint and desktop UI controls so connectivity can be checked directly from the current provider form values.
- Added automated tests for provider health behavior and the provider-health API route.
- Verified the provider health flow with `pytest` and a backend and UI smoke run, then removed the temporary smoke database artifact `data/test_step3_smoke.db`.
- Entered phase B / step 4 and added a runtime controller factory plus a real Windows input controller for mouse move, left click, and text typing.
- Updated the backend and health endpoint to expose which controller is active at runtime instead of always using the noop controller.
- Updated the desktop UI to show the active control engine and to distinguish real execution from noop acceptance in control-action feedback.
- Added controller tests covering Windows input execution paths and runtime controller selection fallback.
- Verified the phase B / step 4 runtime wiring with `pytest` and a backend and UI smoke run, then removed the temporary smoke database artifact `data/test_step4_smoke.db`.
- Entered phase C / step 5 and added config and model support plus perception service modules for screenshot capture, OCR, and text-based element lookup.
- Wired perception endpoints into the backend and added a desktop perception panel for capture, OCR, and text lookup workflows.
- Updated dependency declarations for Pillow and optional RapidOCR support, plus tests for perception routes and merged UI-state persistence.
- Completed phase C / step 5 by fixing the Windows native OCR helper to aggregate line bounding boxes with numeric bounds instead of fragile WinRT rectangle mutation.
- Verified the real perception flow end to end: screenshot capture, Windows OCR on a generated image, and text lookup through the FastAPI routes all succeeded.
- Re-ran the full automated suite with `pytest -q` (`16 passed`) and `ruff check .` after the OCR fix.
- Removed the temporary phase C smoke artifacts: `data/test_step5_smoke.db`, `data/test_step5_ocr.png`, and the generated capture images under `data/captures/`.

## 2026-04-12

- Entered phase F / step 4 and reworked high-risk approvals so workflow tasks now support three inline decisions: `allow`, `deny`, and `prompt`, with a persisted 60-second timeout policy that can default to allow, deny, or a fallback prompt from settings.
- Extended `src/agent_studio/services/workflows/workflow_service.py` so timed-out or manually prompted approvals can skip the risky script, record operator guidance, and carry that guidance forward into later model-driven analysis steps.
- Extended `src/agent_studio/services/automation/permission_manager.py`, `src/agent_studio/services/workflows/workflow_service.py`, and `src/agent_studio/services/backend_server.py` so desktop-control steps in `Ask Every Time` mode also enter the same inline approval queue instead of failing outright or requiring a popup dialog.
- Updated `src/agent_studio/api/routes.py`, `src/agent_studio/core/models.py`, and `src/agent_studio/ui/i18n.py` to expose the new approval decision payload, timeout settings, and inline approval labels/status strings.
- Reworked `src/agent_studio/ui/main_window.py` so task approvals and system-script reviews now stay inside the main window instead of using popup dialogs, with visible countdowns, allow/deny/prompt actions, and timeout-default handling.
- Upgraded the task detail area in `src/agent_studio/ui/main_window.py` to use multiple visual tabs: one tab per opened task plus nested `Overview` and `Agent / Subagent` tabs for recursive agent trees.
- Replaced `用户手册.md` with a refreshed manual that documents inline approvals, timeout defaults, task/agent tab visualization, and the current `/api/tasks/{task_id}/approve` decision contract.
- Re-ran a targeted `.venv\\Scripts\\python.exe` `py_compile` pass for `main_window.py`, `i18n.py`, `routes.py`, and `workflow_service.py`; skipped the full automated test suite in this round.

- Entered phase F / step 3 and upgraded `analyze_image` so the workflow layer now asks the active vision model for a structured JSON result containing both a human-readable summary and a candidate follow-up action chain.
- Added parsing and normalization logic in `src/agent_studio/services/workflows/workflow_service.py` so `analyze_image` results can emit safe suggested steps such as `find_text`, `move_mouse`, `left_click`, and `type_text`, while automatically carrying forward the relevant `image_path`.
- Extended `src/agent_studio/ui/main_window.py` with an `Import Suggestions` action for the selected task, enabled when an `analyze_image` result contains suggested steps, and updated task output rendering to display those candidates inline.
- Updated `src/agent_studio/ui/i18n.py` with strings for suggested-step import and status messages, and updated `用户手册.md` to document that `Analyze Image` can now produce importable suggested action chains.
- Re-ran a targeted `.venv\\Scripts\\python.exe` `py_compile` pass after the phase F / step 3 changes; skipped the full automated test suite in this round.

- Entered phase F / step 2 and extended the task builder so local images can be attached directly to workflow steps such as `run_ocr`, `find_text`, and `analyze_image` without going through chat first.
- Added task-image controls in `src/agent_studio/ui/main_window.py` for choosing a local image or reusing the latest capture, then updated task draft and task-detail rendering to show the selected image source inline with each step.
- Added new task-builder localization entries in `src/agent_studio/ui/i18n.py` for image selection and task-image status feedback.
- Created `用户手册.md` with startup, provider setup, multimodal chat, workflow/agent usage, API examples, safety behavior, and maintenance rules, and established the rule that future code changes must update both the manual and `checklog.md`.
- Re-ran a targeted `.venv\\Scripts\\python.exe` `py_compile` pass for the touched Python files after the task-builder and manual updates; skipped the full automated test suite for this step.

- Entered phase F / step 1 and wired multimodal image attachments into `ChatRequest`, `ChatResponse`, and persisted conversation history so the desktop app and external API callers can send local image paths or inline image payloads through the same `/api/chat` route.
- Updated the provider layer so OpenAI-compatible chat builds image content parts, Ollama chat sends `images` payloads for local vision models such as `qwen3-vl:4b`, and mock responses now report when image inputs were included.
- Switched the default local model to `qwen3-vl:4b`, added SQLite conversation-message attachment persistence with schema migration support, and refreshed the chat route so uploaded image paths also become the latest reusable image context for later perception and workflow steps.
- Extended the desktop UI with lightweight image-attachment controls in the prompt area, image-aware chat history rendering, and chat status feedback that reports when a response used multimodal vision input.
- Promoted multimodal analysis into the agent workflow toolchain by adding `WorkflowStepType.ANALYZE_IMAGE`, wiring `WorkflowService` to the shared `ModelRouter`, and letting agent steps analyze the current screenshot or an API-specified `image_path` with the active provider.
- Ran a targeted syntax pass with `.venv\\Scripts\\python.exe` and `py_compile` across the touched files; skipped the full automated test suite this round because testing was explicitly optional for this request.

- Entered phase C / step 6 and added workflow task models, task persistence methods, and workflow routes for create, list, detail, and run operations.
- Added `src/agent_studio/services/workflows/workflow_service.py` so tasks can execute ordered steps such as capture, OCR, text lookup, mouse movement, clicks, and typing with persisted step results.
- Added a unified settings apply route and persisted UI language support so the app can remember a selected language code such as `zh-CN`, `en-US`, or a custom value like `ja-JP`.
- Refactored the desktop UI to reduce clutter by moving provider, control mode, and language controls into a dedicated settings dialog instead of keeping them all on the main screen.
- Reworked the main workspace into lighter tabs for tasks, perception, and events, and added a task composer plus saved-task panel to drive phase C workflows from the desktop shell.
- Added UI localization helpers in `src/agent_studio/ui/i18n.py` and `src/agent_studio/ui/settings_dialog.py`, then wired chat requests to honor the selected response language.
- Added automated coverage in `tests/test_workflow_routes.py` plus updated persistence tests for stored language preference.
- Verified phase C / step 6 with `py_compile`, `pytest -q` (`18 passed`), and `ruff check .`; removed the temporary smoke database artifact `data/test_step6_smoke.db`.
- Entered phase D / step 1 and extended workflow tasks into recursive agent trees so any agent can own child agents and those child agents can keep delegating further down the tree.
- Added task-agent APIs for creating subagents and fetching an agent tree, then updated workflow execution so task runs recurse through root agents, child agents, and deeper descendants while preserving per-agent status and step results.
- Updated the desktop task panel to show agent counts, render the recursive agent tree in task output, and allow adding a new subagent to the currently selected task.
- Removed the chat UI behavior that forced requests to follow the current interface language, so conversation requests now go out without injecting a language preference prompt.
- Expanded `tests/test_workflow_routes.py` to verify nested child-agent creation, grandchild-agent delegation, recursive execution, and persisted agent counts; re-ran `py_compile`, `pytest -q` (`18 passed`), and `ruff check .`.
- Entered phase D / step 2 and added provider capability profiles plus a new `/api/provider/capabilities` preview route so the settings dialog can inspect the active provider strategy before saving.
- Extended `ModelRouter` fallback behavior to return explicit attempted-provider metadata and to fall back cleanly to the local mock provider when a non-mock provider fails.
- Optimized `src/agent_studio/ui/settings_dialog.py` by adding an output-detail selector (`final_only` vs `step_summary`), a mock-fallback toggle, and an in-dialog capability summary panel.
- Updated `src/agent_studio/ui/main_window.py` so task and perception panels respect the saved output mode, cache and re-render their latest results after settings changes, and show fallback route details in chat status feedback.
- Added localization entries for the new settings and task-agent labels, and removed several remaining hard-coded English strings from the task/settings workflow.
- Added `tests/test_model_router_fallback.py`, expanded provider capability coverage in `tests/test_provider_health.py`, and extended persistence/settings tests for `allow_mock_fallback` and `output_mode`.
- Verified phase D / step 2 with `py_compile`, `.venv\\Scripts\\python.exe -m pytest -q` (`21 passed`), and `.venv\\Scripts\\python.exe -m ruff check .`; `ruff` still prints two environment-level `拒绝访问` warnings but exits successfully.
- Entered phase E / step 1 and introduced a dedicated cross-platform system-agent layer with automatic host detection, Python-first screenshot backends, and high-risk script review/execute APIs.
- Updated screenshot capture to prefer the Python `mss` backend and fall back to Pillow `ImageGrab`, then reordered OCR to prefer Python RapidOCR before using the Windows helper fallback.
- Added `src/agent_studio/services/system/system_service.py` plus `/api/system/info`, `/api/system/script/prepare`, and `/api/system/script/execute` so the app can detect the current OS/runtime and execute reviewed scripts with explicit confirmation tokens.
- Wired the backend and workflow runtime to use the new system service, and added a `detect_system` workflow step so tasks and recursive agents can inspect the host OS before choosing later actions.
- Extended the desktop UI with a dedicated System tab for runtime inspection and reviewed script execution, keeping the main workspace less cluttered while adding agent-like system controls.
- Added the new translation keys and runtime/script UI strings, and updated `pyproject.toml` to declare `mss` as a Python screenshot dependency.
- Installed `mss` into the project `.venv` after resolving temp-directory permission issues with a workspace-local temp path so the Python screenshot backend is available immediately in the current environment.
- Added `tests/test_system_service.py`, expanded `tests/test_workflow_routes.py` with the `detect_system` step, and re-ran `py_compile`, `.venv\\Scripts\\python.exe -m pytest -q` (`23 passed`), and `.venv\\Scripts\\python.exe -m ruff check .`; `ruff` still emits environment-level `拒绝访问` warnings but succeeds.
- Entered phase E / step 2 and integrated `execute_script` into workflow agents with a pending-approval queue, a `waiting_approval` task state, an approval route, and resume-after-approval execution so high-risk steps can be reviewed before continuing.
- Updated workflow persistence and task rendering to store `pending_approval` details, show waiting approvals in task output, and expose a desktop approval button for the currently selected task.
- Added `WorkflowStepType.EXECUTE_SCRIPT`, `WorkflowApprovalDecisionRequest`, and related workflow-task model fields so scripted agent actions can be represented and resumed cleanly.
- Installed Ollama from the official installer, pulled the local `qwen3-vl:4b` multimodal model, and verified that `ollama list` now shows the model locally.
- Performed a real multimodal smoke test against `http://127.0.0.1:11434/api/chat` using a generated local image; `qwen3-vl:4b` correctly described the image and quoted the visible text.
- Verified the app-side local connector as well: `OllamaProvider.health_check` succeeded for `qwen3-vl:4b`, and a direct provider `generate` call returned `local qwen ok`.
- Added workflow approval coverage in `tests/test_workflow_routes.py`, re-ran `py_compile`, `.venv\\Scripts\\python.exe -m pytest -q` (`24 passed`), and `.venv\\Scripts\\python.exe -m ruff check .`; `ruff` still emits environment-level `拒绝访问` warnings but exits successfully.
