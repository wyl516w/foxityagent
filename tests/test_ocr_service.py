import json
from pathlib import Path
import subprocess

from agent_studio.services.perception import ocr_service
from agent_studio.services.perception.ocr_service import OcrService


def test_ocr_service_parses_windows_ocr_output(monkeypatch) -> None:
    service = OcrService()
    service._windows_script = Path(__file__)

    payload = {
        "ok": True,
        "engine": "windows_media_ocr",
        "lines": [
            {
                "text": "Open Settings",
                "score": 1.0,
                "bbox": [[10, 10], [110, 10], [110, 40], [10, 40]],
            }
        ],
        "message": "OCR completed with 1 text lines.",
    }

    monkeypatch.setattr(ocr_service.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        ocr_service.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        ),
    )

    result = service.extract_text(str(Path(__file__)))

    assert result.ok is True
    assert result.engine == "windows_media_ocr"
    assert result.lines[0].text == "Open Settings"
