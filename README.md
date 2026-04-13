# Agent Studio

Agent Studio is a cross-platform desktop skeleton for an AI-assisted automation product.
This starter focuses on three things first:

- a desktop UI that can be extended safely
- a local backend with clear API boundaries
- model providers that can switch between cloud APIs and local deployment

## Stack

- Desktop UI: PySide6
- Local backend: FastAPI + Uvicorn
- Model routing: OpenAI-compatible APIs, Ollama, and a mock provider
- Automation safety: explicit desktop-control permission modes with a non-invasive demo controller

## Quick start

```powershell
.\scripts\setup.ps1
Copy-Item .env.example .env
.\scripts\run.ps1
```

## What is included

- provider abstraction for remote or local LLM access
- a desktop chat/control panel
- a FastAPI service running in the same app process
- a permission manager for desktop control
- a safe demo automation endpoint that only logs simulated actions

## Next milestones

The detailed roadmap is in `实现计划.md`.
