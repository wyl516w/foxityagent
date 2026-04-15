from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request

from fastapi import FastAPI
import uvicorn

from agent_studio.api.routes import build_router
from agent_studio.core.config import AppConfig
from agent_studio.core.state import SharedState
from agent_studio.services.automation.controller_factory import build_input_controller
from agent_studio.services.automation.permission_manager import PermissionManager
from agent_studio.services.conversation_service import ConversationService
from agent_studio.services.desktop import DesktopAgentRuntime
from agent_studio.services.model_router import ModelRouter
from agent_studio.services.perception.perception_service import PerceptionService
from agent_studio.services.system.system_service import SystemService
from agent_studio.services.workflows.workflow_service import WorkflowService


class BackendServer:
    def __init__(self, config: AppConfig, state: SharedState) -> None:
        self.config = config
        self.state = state
        self.permission_manager = PermissionManager(state=state)
        self.input_controller = build_input_controller(
            state=state,
            permission_manager=self.permission_manager,
        )
        self.model_router = ModelRouter(config=config, state=state)
        self.conversation_service = (
            ConversationService(store=state.store) if state.store is not None else None
        )
        self.perception_service = PerceptionService(config=config)
        self.desktop_runtime = DesktopAgentRuntime(
            state=state,
            perception_service=self.perception_service,
            input_controller=self.input_controller,
            model_router=self.model_router,
        )
        self.system_service = SystemService(
            config=config,
            perception_service=self.perception_service,
            state=state,
            model_router=self.model_router,
        )
        self.workflow_service = (
            WorkflowService(
                store=state.store,
                state=state,
                perception_service=self.perception_service,
                input_controller=self.input_controller,
                permission_manager=self.permission_manager,
                system_service=self.system_service,
                model_router=self.model_router,
            )
            if state.store is not None
            else None
        )
        self.app = self._build_app()
        self._server = uvicorn.Server(
            uvicorn.Config(
                self.app,
                host=config.backend_host,
                port=config.backend_port,
                log_level="warning",
            )
        )
        self._thread: threading.Thread | None = None

    def _build_app(self) -> FastAPI:
        app = FastAPI(title=self.config.app_name)
        app.include_router(
            build_router(
                config=self.config,
                state=self.state,
                model_router=self.model_router,
                permission_manager=self.permission_manager,
                input_controller=self.input_controller,
                conversation_service=self.conversation_service,
                perception_service=self.perception_service,
                desktop_runtime=self.desktop_runtime,
                workflow_service=self.workflow_service,
                system_service=self.system_service,
            )
        )
        return app

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        if not self.wait_until_ready(timeout_seconds=8.0):
            self._server.should_exit = True
            self._thread.join(timeout=5.0)
            self._thread = None
            message = "Backend server failed to become ready within 8 seconds."
            self.state.append_event(message)
            raise RuntimeError(message)
        self.state.append_event(
            f"Backend server started with {self.input_controller.controller_name} controller."
        )

    def wait_until_ready(self, timeout_seconds: float) -> bool:
        deadline = time.time() + timeout_seconds
        health_url = f"{self.config.backend_url}/api/health"

        while time.time() < deadline:
            try:
                with urllib.request.urlopen(health_url, timeout=1.0) as response:
                    if response.status == 200:
                        return True
            except urllib.error.URLError:
                time.sleep(0.2)

        return False

    def stop(self) -> None:
        if not self._thread:
            return

        self._server.should_exit = True
        self._thread.join(timeout=5.0)
        self.state.append_event("Backend server stopped.")
