from __future__ import annotations

from pathlib import Path

from agent_studio.core.config import AppConfig
from agent_studio.core.models import (
    ElementLookupResponse,
    OcrResponse,
    ScreenshotResponse,
)
from agent_studio.services.perception.element_locator import ElementLocator
from agent_studio.services.perception.ocr_service import OcrService
from agent_studio.services.perception.screenshot_service import ScreenshotService


class PerceptionService:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._screenshot_service = ScreenshotService(config=config)
        self._ocr_service = OcrService()
        self._element_locator = ElementLocator()

    def capture_screen(self) -> ScreenshotResponse:
        return self._screenshot_service.capture_screen()

    def run_ocr(self, image_path: str) -> OcrResponse:
        return self._ocr_service.extract_text(image_path)

    def find_text(
        self,
        image_path: str,
        query: str,
        case_sensitive: bool = False,
    ) -> ElementLookupResponse:
        ocr_response = self.run_ocr(image_path)
        return self._element_locator.find_text(
            ocr_response=ocr_response,
            query=query,
            case_sensitive=case_sensitive,
        )

    @property
    def captures_dir(self) -> Path:
        return self._config.captures_dir

    @property
    def screenshot_backend_name(self) -> str:
        return self._screenshot_service.backend_name

    @property
    def ocr_backend_name(self) -> str:
        return self._ocr_service.engine_name
