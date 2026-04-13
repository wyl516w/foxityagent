from __future__ import annotations

import json
from pathlib import Path
import platform
import subprocess
from typing import Any

from agent_studio.core.models import OcrResponse, OcrTextLine


class OcrService:
    def __init__(self) -> None:
        self._engine = None
        self._engine_name = "unavailable"
        self._windows_script = (
            Path(__file__).resolve().parents[4] / "scripts" / "windows_ocr.ps1"
        )

    @property
    def engine_name(self) -> str:
        return self._engine_name

    def extract_text(self, image_path: str) -> OcrResponse:
        resolved_path = Path(image_path)
        if not resolved_path.exists():
            return OcrResponse(
                ok=False,
                image_path=str(resolved_path),
                engine=self.engine_name,
                message="The requested image does not exist.",
            )

        engine = self._get_engine()
        if engine is not None:
            try:
                result, _ = engine(str(resolved_path))
                lines = [self._parse_line(item) for item in result or []]
                return OcrResponse(
                    ok=True,
                    image_path=str(resolved_path),
                    engine=self.engine_name,
                    lines=lines,
                    message=f"OCR completed with {len(lines)} text lines.",
                )
            except Exception as exc:
                python_error = exc
            else:
                python_error = None
        else:
            python_error = None

        if platform.system() == "Windows":
            windows_result = self._extract_with_windows_ocr(resolved_path)
            if windows_result.ok:
                return windows_result
            if engine is None:
                return windows_result
            return OcrResponse(
                ok=False,
                image_path=str(resolved_path),
                engine=self.engine_name,
                message=(
                    f"Python OCR failed: {python_error}. Windows fallback also failed: "
                    f"{windows_result.message}"
                ),
            )

        if engine is None:
            return OcrResponse(
                ok=False,
                image_path=str(resolved_path),
                engine=self.engine_name,
                message=(
                    "OCR engine is unavailable. Install rapidocr_onnxruntime to enable OCR."
                ),
            )

        return OcrResponse(
            ok=False,
            image_path=str(resolved_path),
            engine=self.engine_name,
            message=f"OCR failed: {python_error}",
        )

    def _get_engine(self):
        if self._engine is not None:
            return self._engine

        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError:
            self._engine_name = "unavailable"
            return None

        self._engine = RapidOCR()
        self._engine_name = "rapidocr_onnxruntime"
        return self._engine

    def _extract_with_windows_ocr(self, image_path: Path) -> OcrResponse:
        if not self._windows_script.exists():
            return OcrResponse(
                ok=False,
                image_path=str(image_path),
                engine="windows_media_ocr",
                message="The Windows OCR helper script was not found.",
            )

        try:
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(self._windows_script),
                    "-ImagePath",
                    str(image_path),
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except Exception as exc:
            return OcrResponse(
                ok=False,
                image_path=str(image_path),
                engine="windows_media_ocr",
                message=f"Windows OCR invocation failed: {exc}",
            )

        output = (completed.stdout or completed.stderr).strip()
        if not output:
            return OcrResponse(
                ok=False,
                image_path=str(image_path),
                engine="windows_media_ocr",
                message="Windows OCR returned no output.",
            )

        try:
            payload = json.loads(output)
        except json.JSONDecodeError as exc:
            return OcrResponse(
                ok=False,
                image_path=str(image_path),
                engine="windows_media_ocr",
                message=f"Windows OCR returned invalid JSON: {exc}",
            )

        lines = [
            OcrTextLine(
                text=str(item.get("text", "")),
                score=float(item.get("score", 0.0)),
                bbox=item.get("bbox", []),
            )
            for item in payload.get("lines", [])
            if isinstance(item, dict)
        ]
        self._engine_name = "windows_media_ocr"
        return OcrResponse(
            ok=bool(payload.get("ok")),
            image_path=str(image_path),
            engine="windows_media_ocr",
            lines=lines,
            message=str(payload.get("message", "Windows OCR finished.")),
        )

    @staticmethod
    def _parse_line(item: Any) -> OcrTextLine:
        bbox: list[list[int]] = []
        text = ""
        score = 0.0

        if isinstance(item, (list, tuple)) and len(item) >= 2:
            raw_box = item[0]
            raw_text = item[1]
            if isinstance(raw_box, (list, tuple)):
                bbox = [
                    [int(point[0]), int(point[1])]
                    for point in raw_box
                    if isinstance(point, (list, tuple)) and len(point) >= 2
                ]
            if isinstance(raw_text, (list, tuple)) and len(raw_text) >= 1:
                text = str(raw_text[0])
                if len(raw_text) >= 2:
                    try:
                        score = float(raw_text[1])
                    except (TypeError, ValueError):
                        score = 0.0
            else:
                text = str(raw_text)

        return OcrTextLine(text=text, score=score, bbox=bbox)
