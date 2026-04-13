# Check Log

## 2026-04-13

- Entered phase G / step 2 and replaced `src/agent_studio/ui/main_window.py` with a conversation-first main window.
- Removed the old workflow-first panels and duplicate method definitions from `src/agent_studio/ui/main_window.py`, keeping only the conversation list, chat composer, session task tabs, agent tabs, inline approval controls, and recent events.
- Added request de-duplication and safer chat sending in `src/agent_studio/ui/main_window.py` so periodic refreshes do not pile up duplicate threads and failed sends no longer clear the user's draft input.
- Replaced `src/agent_studio/ui/i18n.py` with a cleaned translation table for the simplified chat-first UI and the existing settings dialog.
- Added `_build_chat_task_response(...)` to `src/agent_studio/api/routes.py` so `/api/chat` returns a valid autonomous-task response when workflow execution is enabled.
- Extended `tests/test_workflow_routes.py` with a regression test covering `/api/chat` when the workflow service is enabled, including automatic task creation inside a conversation.
- Rewrote `实现计划.md` and `用户手册.md` to match the new stage status and the simplified chat-first UI.
- Verified this round with `.venv\\Scripts\\python.exe -m py_compile`, `pytest -q tests/test_workflow_routes.py tests/test_conversation_history_api.py` (`6 passed`), and `.venv\\Scripts\\python.exe -m ruff check src tests`.

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

## Summary

- Earlier detailed entries were consolidated into this cleaner stage log during phase G / step 2 so future changes can be tracked without the previous encoding-corrupted history.
