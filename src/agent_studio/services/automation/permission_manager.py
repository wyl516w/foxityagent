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
            remaining = self._one_time_approvals[reason]
        self._audit(
            action=reason,
            decision="approve_once",
            details={
                "remaining_approvals": remaining,
            },
        )

    def evaluate(self, reason: str) -> PermissionDecisionPayload:
        mode = self._state.get_automation_settings().control_mode

        if mode == ControlMode.DENY:
            decision = PermissionDecisionPayload(
                allowed=False,
                requires_confirmation=False,
                mode=mode,
                message="Desktop control is disabled.",
            )
            self._audit_decision(reason=reason, decision="deny", payload=decision)
            return decision

        if mode == ControlMode.ASK:
            with self._lock:
                remaining = self._one_time_approvals.get(reason, 0)
                if remaining > 0:
                    next_remaining = remaining - 1
                    if next_remaining > 0:
                        self._one_time_approvals[reason] = next_remaining
                    else:
                        self._one_time_approvals.pop(reason, None)
                    decision = PermissionDecisionPayload(
                        allowed=True,
                        requires_confirmation=False,
                        mode=mode,
                        message="Desktop control was approved inline for one action.",
                    )
                    self._audit_decision(
                        reason=reason,
                        decision="allow_once",
                        payload=decision,
                        extra={"remaining_approvals": next_remaining},
                    )
                    return decision
            decision = PermissionDecisionPayload(
                allowed=False,
                requires_confirmation=True,
                mode=mode,
                message=(
                    "This build is set to ask every time. "
                    f"Add an explicit approval dialog before running action: {reason}."
                ),
            )
            self._audit_decision(reason=reason, decision="ask", payload=decision)
            return decision

        if mode == ControlMode.ALLOW_SESSION:
            decision = PermissionDecisionPayload(
                allowed=True,
                requires_confirmation=False,
                mode=mode,
                message="Desktop control is allowed for the current session.",
            )
            self._audit_decision(reason=reason, decision="allow_session", payload=decision)
            return decision

        decision = PermissionDecisionPayload(
            allowed=True,
            requires_confirmation=False,
            mode=mode,
            message="Desktop control is always allowed.",
        )
        self._audit_decision(reason=reason, decision="allow_always", payload=decision)
        return decision

    def _audit_decision(
        self,
        *,
        reason: str,
        decision: str,
        payload: PermissionDecisionPayload,
        extra: dict | None = None,
    ) -> None:
        details = payload.model_dump(mode="json")
        if extra:
            details.update(extra)
        self._audit(action=reason, decision=decision, details=details)

    def _audit(
        self,
        *,
        action: str,
        decision: str,
        details: dict,
    ) -> None:
        store = self._state.store
        if store is None:
            return
        store.append_permission_audit(
            action=action,
            decision=decision,
            details=details,
        )
