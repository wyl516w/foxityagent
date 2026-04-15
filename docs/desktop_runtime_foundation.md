# Desktop Runtime Foundation

This patch starts correcting the product direction away from a workflow-shaped shell and toward a desktop-agent runtime.

## What was added

- `DesktopAgentRuntime` in `src/agent_studio/services/desktop/runtime.py`
- a direct `observe -> summarize -> recommend one next action -> optionally execute` loop
- a parser that normalizes strict-JSON planner output into a single next action

## Why this matters

The existing project is already reasonably strong in conversation history, task persistence, permission auditing, and approval plumbing.

The weak point is the desktop loop itself.

This runtime gives the codebase a standalone foundation that does not require the workflow service to remain the primary execution abstraction.

## Remaining work

- wire the runtime into `BackendServer` and `routes.py`
- expose the runtime in the desktop UI, ideally alongside a visible screen canvas or overlay
- expand the runtime from one-shot stepping to a persistent multi-turn desktop session
- add richer action kinds beyond the current baseline set
- add accessibility-backed or grounded element lookup so action targets are not purely prompt-derived
