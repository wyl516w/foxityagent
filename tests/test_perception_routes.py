from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_studio.api.routes import build_router
from agent_studio.core.config import AppConfig
from agent_studio.core.models import (
    ElementLookupResponse,
    ElementMatch,
    OcrResponse,
    OcrTextLine,
    ScreenshotResponse,
)
from agent_studio.core.state import SharedState
from agent_studio.services.automation.noop_controller import NoopInputController
from agent_studio.services.automation.permission_manager import PermissionManager
from agent_studio.services.model_router import ModelRouter


class _StubPerceptionService:
    def capture_screen(self):
        return ScreenshotResponse(
            ok=True,
            image_path="C:/tmp/capture.png",
            width=1920,
            height=1080,
            message="Screenshot captured successfully.",
        )

    def run_ocr(self, image_path: str):
        return OcrResponse(
            ok=True,
            image_path=image_path,
            engine="stub",
            lines=[
                OcrTextLine(
                    text="Open Settings",
                    score=0.99,
                    bbox=[[10, 10], [110, 10], [110, 40], [10, 40]],
                )
            ],
            message="OCR completed with 1 text lines.",
        )

    def find_text(self, image_path: str, query: str, case_sensitive: bool = False):
        return ElementLookupResponse(
            ok=True,
            image_path=image_path,
            query=query,
            matches=[
                ElementMatch(
                    text="Open Settings",
                    score=0.99,
                    bbox=[[10, 10], [110, 10], [110, 40], [10, 40]],
                    center_x=60,
                    center_y=25,
                )
            ],
            message="Found 1 matches for 'Settings'.",
        )


def test_perception_routes_update_latest_capture_and_support_ocr_lookup() -> None:
    config = AppConfig()
    state = SharedState(config=config)
    permission_manager = PermissionManager(state=state)
    app = FastAPI()
    app.include_router(
        build_router(
            config=config,
            state=state,
            model_router=ModelRouter(config=config, state=state),
            permission_manager=permission_manager,
            input_controller=NoopInputController(
                state=state,
                permission_manager=permission_manager,
            ),
            conversation_service=None,
            perception_service=_StubPerceptionService(),
        )
    )
    client = TestClient(app)

    capture_response = client.post("/api/perception/capture", json={})
    assert capture_response.status_code == 200
    assert capture_response.json()["image_path"] == "C:/tmp/capture.png"

    settings_response = client.get("/api/settings")
    assert settings_response.status_code == 200
    assert settings_response.json()["ui"]["latest_capture_path"] == "C:/tmp/capture.png"

    ocr_response = client.post("/api/perception/ocr", json={"image_path": None})
    assert ocr_response.status_code == 200
    assert ocr_response.json()["lines"][0]["text"] == "Open Settings"

    lookup_response = client.post(
        "/api/perception/find",
        json={"query": "Settings", "image_path": None, "case_sensitive": False},
    )
    assert lookup_response.status_code == 200
    match = lookup_response.json()["matches"][0]
    assert match["center_x"] == 60
    assert match["center_y"] == 25
