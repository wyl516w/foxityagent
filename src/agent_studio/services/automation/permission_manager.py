from __future__ import annotations

from threading import RLock

from agent_studio.core.models import ControlMode, PermissionDecisionPayload
from agent_studio.core.state import SharedState


class PermissionManager:
    def __init__(self, state: SharedState) -> None:
        self._state = state
        self._lock = RLock()
        self._one_time_approvals: dict[str, int] = {}

    def approve_once(self, reason: str) -> None:
        with self._lock:
            self._one_time_approvals[reason] = self._one_time_approvals.get(reason, 0) + 1

    def evaluate(self, reason: str) -> PermissionDecisionPayload:
        mode = self._state.get_automation_settings().control_mode

        if mode == ControlMode.DENY:
            return PermissionDecisionPayload(
                allowed=False,
                requires_confirmation=False,
                mode=mode,
                message="Desktop control is disabled.",
            )

        if mode == ControlMode.ASK:
            with self._lock:
                remaining = self._one_time_approvals.get(reason, 0)
                if remaining > 0:
                    next_remaining = remaining - 1
                    if next_remaining > 0:
                        self._one_time_approvals[reason] = next_remaining
                    else:
                        self._one_time_approvals.pop(reason, None)
                    return PermissionDecisionPayload(
                        allowed=True,
                        requires_confirmation=False,
                        mode=mode,
                        message="Desktop control was approved inline for one action.",
                    )
            return PermissionDecisionPayload(
                allowed=False,
                requires_confirmation=True,
                mode=mode,
                message=(
                    "This build is set to ask every time. "
                    f"Add an explicit approval dialog before running action: {reason}."
                ),
            )

        if mode == ControlMode.ALLOW_SESSION:
            return PermissionDecisionPayload(
                allowed=True,
                requires_confirmation=False,
                mode=mode,
                message="Desktop control is allowed for the current session.",
            )

        return PermissionDecisionPayload(
            allowed=True,
            requires_confirmation=False,
            mode=mode,
            message="Desktop control is always allowed.",
        )
