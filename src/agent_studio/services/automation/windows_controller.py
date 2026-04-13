from __future__ import annotations

import ctypes
import re
from ctypes import wintypes

from agent_studio.core.models import ControlActionPayload, ControlActionResult, ControlActionType
from agent_studio.core.state import SharedState
from agent_studio.services.automation.input_controller import InputController
from agent_studio.services.automation.permission_manager import PermissionManager


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
ULONG_PTR = wintypes.WPARAM


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", INPUT_UNION),
    ]


class WindowsInputController(InputController):
    def __init__(
        self,
        state: SharedState,
        permission_manager: PermissionManager,
        user32=None,
    ) -> None:
        self._state = state
        self._permission_manager = permission_manager
        self._user32 = user32 or ctypes.WinDLL("user32", use_last_error=True)

    @property
    def controller_name(self) -> str:
        return "windows_real"

    def execute(self, payload: ControlActionPayload) -> ControlActionResult:
        decision = self._permission_manager.evaluate(reason=payload.action.value)
        if not decision.allowed:
            return ControlActionResult(
                allowed=False,
                executed=False,
                message=decision.message,
                event=f"blocked:{payload.action.value}",
            )

        try:
            if payload.action == ControlActionType.MOVE_MOUSE:
                x, y = self._parse_coordinates(payload.text)
                self._move_mouse(x, y)
                detail = f"{x},{y}"
                message = f"Mouse moved to ({x}, {y})."
            elif payload.action == ControlActionType.LEFT_CLICK:
                self._left_click()
                detail = "left_click"
                message = "Left click executed."
            elif payload.action == ControlActionType.TYPE_TEXT:
                text = (payload.text or "").strip()
                if not text:
                    raise ValueError("type_text requires a non-empty text payload.")
                self._send_text(text)
                detail = f"text:{len(text)}"
                message = f"Typed {len(text)} characters."
            else:  # pragma: no cover - enum guards this
                raise ValueError(f"Unsupported action: {payload.action}")
        except Exception as exc:
            self._state.append_event(
                f"Windows controller failed {payload.action.value}: {exc}"
            )
            return ControlActionResult(
                allowed=True,
                executed=False,
                message=str(exc),
                event=f"failed:{payload.action.value}",
            )

        self._state.append_event(
            f"Windows controller executed {payload.action.value} ({detail})."
        )
        return ControlActionResult(
            allowed=True,
            executed=True,
            message=message,
            event=f"executed:{payload.action.value}:{detail}",
        )

    def _move_mouse(self, x: int, y: int) -> None:
        if not self._user32.SetCursorPos(int(x), int(y)):
            raise OSError("SetCursorPos failed.")

    def _left_click(self) -> None:
        inputs = (INPUT * 2)()
        inputs[0].type = INPUT_MOUSE
        inputs[0].mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, 0)
        inputs[1].type = INPUT_MOUSE
        inputs[1].mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, 0)
        sent = self._user32.SendInput(2, inputs, ctypes.sizeof(INPUT))
        if sent != 2:
            raise OSError("SendInput failed for left click.")

    def _send_text(self, text: str) -> None:
        for character in text:
            self._send_unicode_char(character)

    def _send_unicode_char(self, character: str) -> None:
        inputs = (INPUT * 2)()
        scan_code = ord(character)

        inputs[0].type = INPUT_KEYBOARD
        inputs[0].ki = KEYBDINPUT(0, scan_code, KEYEVENTF_UNICODE, 0, 0)
        inputs[1].type = INPUT_KEYBOARD
        inputs[1].ki = KEYBDINPUT(
            0,
            scan_code,
            KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
            0,
            0,
        )

        sent = self._user32.SendInput(2, inputs, ctypes.sizeof(INPUT))
        if sent != 2:
            raise OSError(f"SendInput failed while typing '{character}'.")

    @staticmethod
    def _parse_coordinates(value: str | None) -> tuple[int, int]:
        if value is None or not value.strip():
            raise ValueError("move_mouse requires coordinates like '640,360'.")
        parts = [part for part in re.split(r"[\s,]+", value.strip()) if part]
        if len(parts) != 2:
            raise ValueError("move_mouse requires coordinates like '640,360'.")
        try:
            x = int(parts[0])
            y = int(parts[1])
        except ValueError as exc:
            raise ValueError("move_mouse coordinates must be integers.") from exc
        if x < 0 or y < 0:
            raise ValueError("move_mouse coordinates must be non-negative.")
        return x, y
