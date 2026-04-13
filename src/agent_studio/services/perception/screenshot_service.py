from __future__ import annotations

from datetime import datetime

from agent_studio.core.config import AppConfig
from agent_studio.core.models import ScreenshotResponse


class ScreenshotService:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._backend_name = "unavailable"

    @property
    def backend_name(self) -> str:
        return self._backend_name

    def capture_screen(self) -> ScreenshotResponse:
        mss_error: Exception | None = None
        image = None

        try:
            image = self._capture_with_mss()
        except Exception as exc:
            mss_error = exc

        if image is None:
            try:
                image = self._capture_with_pillow()
            except ImportError:
                message = "Python screenshot backends are unavailable. Install Pillow or mss."
                if mss_error is not None:
                    message = f"{message} Last error: {mss_error}"
                return ScreenshotResponse(ok=False, message=message)
            except Exception as exc:
                return ScreenshotResponse(
                    ok=False,
                    message=f"Screenshot capture failed: {exc}",
                )

        self._config.captures_dir.mkdir(parents=True, exist_ok=True)
        filename = f"capture-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.png"
        target_path = self._config.captures_dir / filename
        image.save(target_path)
        return ScreenshotResponse(
            ok=True,
            image_path=str(target_path.resolve()),
            width=image.width,
            height=image.height,
            message=f"Screenshot captured successfully via {self._backend_name}.",
        )

    def _capture_with_mss(self):
        try:
            import mss
            from PIL import Image
        except ImportError as exc:
            raise ImportError("mss backend is unavailable.") from exc

        with mss.mss() as screen_capture:
            screenshot = screen_capture.grab(screen_capture.monitors[0])
            image = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
        self._backend_name = "mss"
        return image

    def _capture_with_pillow(self):
        from PIL import ImageGrab

        image = ImageGrab.grab(all_screens=True)
        self._backend_name = "pillow_imagegrab"
        return image
