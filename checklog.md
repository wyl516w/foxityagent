# Check Log

## 2026-04-13

- Entered phase G / step 4 and refined the right-hand workspace in `src/agent_studio/ui/main_window.py` so it reads more like a conversation execution view than a workflow console.
- Updated the right-side run tabs to be status-first, removed the raw task ID from the visible summary, and re-labeled the run and agent sections around active agents, recent actions, child agents, and approval state instead of workflow-first wording.
- Replaced the corrupted translation table in `src/agent_studio/ui/i18n.py` with a clean UTF-8 language map for English and Simplified Chinese, including the updated conversation-first wording for the main shell and settings dialog.
- Updated `实现计划.md` and `用户手册.md` so the current stage now reflects the cleaner conversation execution view and the repaired bilingual UI text.
- Verified phase G / step 4 with `.venv\\Scripts\\python.exe -m py_compile src/agent_studio/ui/main_window.py src/agent_studio/ui/i18n.py src/agent_studio/ui/settings_dialog.py` and `.venv\\Scripts\\python.exe -m ruff check src/agent_studio/ui/main_window.py src/agent_studio/ui/i18n.py src/agent_studio/ui/settings_dialog.py`.

- Resolved the `git pull` merge conflicts in `实现计划.md`, `用户手册.md`, and `checklog.md` by keeping the local conversation-first product direction and rewriting the files cleanly.
- Entered phase G / step 3 and updated `src/agent_studio/api/routes.py` so `/api/chat` now creates autonomous conversation tasks with the current configured provider settings instead of hardcoding the local Ollama route.
- Updated `src/agent_studio/services/backend_server.py` so `BackendServer.start()` fails fast when `/api/health` does not become ready within eight seconds, instead of logging a false-success startup event.
- Reduced event noise in `src/agent_studio/core/state.py` and `src/agent_studio/storage/sqlite_store.py` by removing the generic `UI state updated.` spam and by avoiding a database read-after-write for every appended event.
- Added permission audit persistence in `src/agent_studio/services/automation/permission_manager.py` and `src/agent_studio/storage/sqlite_store.py`, so desktop-control decisions now land in the existing `permission_audit` table.
- Optimized conversation task queries in `src/agent_studio/storage/sqlite_store.py` and `src/agent_studio/services/workflows/workflow_service.py` so conversation filters are pushed into SQLite and conversation detail loading no longer uses the earlier N+1 pattern.
- Repaired test drift in `tests/test_system_service.py` and added regression coverage in `tests/test_backend_server.py`, `tests/test_permission_manager.py`, and `tests/test_workflow_routes.py` for fail-fast startup, permission-audit persistence, and provider-inheriting chat tasks.
- Verified this round with `.venv\\Scripts\\python.exe -m py_compile`, `pytest -q tests/test_workflow_routes.py tests/test_system_service.py tests/test_permission_manager.py tests/test_backend_server.py` (`11 passed`), and `.venv\\Scripts\\python.exe -m ruff check src tests`.

- Entered phase G / step 2 and replaced `src/agent_studio/ui/main_window.py` with a conversation-first main window.
- Removed the old workflow-first panels and duplicate method definitions from `src/agent_studio/ui/main_window.py`, keeping only the conversation list, chat composer, session task tabs, agent tabs, inline approval controls, and recent events.
- Added request de-duplication and safer chat sending in `src/agent_studio/ui/main_window.py` so periodic refreshes do not pile up duplicate threads and failed sends no longer clear the user's draft input.
- Replaced `src/agent_studio/ui/i18n.py` with a cleaned translation table for the simplified chat-first UI and the existing settings dialog.
- Added `_build_chat_task_response(...)` to `src/agent_studio/api/routes.py` so `/api/chat` returns a valid autonomous-task response when workflow execution is enabled.
- Extended `tests/test_workflow_routes.py` with a regression test covering `/api/chat` when the workflow service is enabled, including automatic task creation inside a conversation.
- Rewrote `实现计划.md` and `用户手册.md` to match the new stage status and the simplified chat-first UI.
- Verified phase G / step 2 with `.venv\\Scripts\\python.exe -m py_compile`, `pytest -q tests/test_workflow_routes.py tests/test_conversation_history_api.py` (`6 passed`), and `.venv\\Scripts\\python.exe -m ruff check src tests`.

- Entered phase G / step 1 and rewrote `实现计划.md` to formally shift the project from a workflow-first prototype toward a conversation-driven desktop agent workbench.
- Added two explicit planning tracks to `实现计划.md`: one for immediate code-governance cleanup and one for the product-level transition to a conversation-centered agent loop.
- Replaced `用户手册.md` with a clean current-state manual that reflects the new product direction without claiming the conversation-first UI refactor was already complete.

## 2026-04-12

- Completed phase F / step 7 and wired the chat composer into a one-click local autonomous-task launcher that defaulted to `Ollama / qwen3-vl:4b`.
- Completed phase F / step 6 and added multi-model agent routing plus full provider connectivity checks, including local Ollama.
- Completed phase F / step 5 and added autonomous workflow execution so tasks and agents no longer required humans to predefine every step before running.
- Completed phase F / step 4 and reworked high-risk approvals into inline decisions with timeout defaults and visible task / agent tabs.
- Completed phase F / step 3 and upgraded `analyze_image` so it can generate structured suggested next steps.
- Completed phase F / step 2 and extended the task builder so local images could be attached directly to visual workflow steps.
- Completed phase F / step 1 and wired multimodal image attachments into `/api/chat`, conversation history, and the workflow toolchain.
- Completed phase E / step 2 and integrated `execute_script` into workflow agents with approval gates, then deployed and verified local `Ollama / qwen3-vl:4b`.

## 2026-04-11

- Completed phase A / step 1 with SQLite persistence for settings, UI state, events, conversations, and tasks.
- Completed phase A / step 2 with persisted conversation history and UI-state synchronization.
- Completed phase A / step 3 with provider connectivity checks for Mock, OpenAI-compatible, and Ollama routes.
- Completed phase B / step 4 with a runtime controller factory and a real Windows input controller.
- Completed phase C / step 5 with screenshot capture, OCR, and text-based element lookup.
- Completed phase C / step 6 with persisted workflow tasks, execution routes, UI language persistence, and the first task-oriented desktop shell.
