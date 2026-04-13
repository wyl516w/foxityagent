from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from threading import RLock
from uuid import uuid4

from agent_studio.core.config import AppConfig
from agent_studio.core.models import (
    ChatRequest,
    ProviderSettingsPayload,
    ScriptExecutionPrepareRequest,
    ScriptExecutionPreviewResponse,
    ScriptExecutionResponse,
    ScriptExecutionRunRequest,
    ScriptRiskLevel,
    ScriptRuntime,
    SystemInfoResponse,
)
from agent_studio.core.state import SharedState
from agent_studio.services.model_router import ModelRouter
from agent_studio.services.perception.perception_service import PerceptionService


@dataclass
class _PendingScriptApproval:
    confirmation_id: str
    script: str
    runtime: ScriptRuntime
    shell_label: str
    timeout_seconds: float
    approval_timeout_seconds: float
    risk_level: ScriptRiskLevel
    summary: str
    warnings: list[str]
    created_at: datetime


class SystemService:
    def __init__(
        self,
        config: AppConfig,
        perception_service: PerceptionService,
        *,
        state: SharedState | None = None,
        model_router: ModelRouter | None = None,
    ) -> None:
        self._config = config
        self._perception_service = perception_service
        self._state = state
        self._model_router = model_router
        self._lock = RLock()
        self._pending: dict[str, _PendingScriptApproval] = {}

    def get_system_info(self) -> SystemInfoResponse:
        runtime = self._preferred_runtime()
        return SystemInfoResponse(
            os_name=platform.system() or "Unknown",
            os_release=platform.release() or "Unknown",
            os_version=platform.version() or "Unknown",
            machine=platform.machine() or "Unknown",
            python_version=platform.python_version(),
            preferred_script_runtime=runtime,
            preferred_shell=self._preferred_shell_label(runtime),
            screenshot_backend=getattr(
                self._perception_service,
                "screenshot_backend_name",
                "unknown",
            ),
            ocr_backend=getattr(
                self._perception_service,
                "ocr_backend_name",
                "unknown",
            ),
        )

    async def prepare_script_execution(
        self,
        request: ScriptExecutionPrepareRequest,
    ) -> ScriptExecutionPreviewResponse:
        self._purge_expired()
        runtime = self._resolve_runtime(request.runtime, request.script)
        shell_label = self._preferred_shell_label(runtime)
        heuristic_warnings = self._script_warnings(request.script)
        heuristic_risk = self._risk_level_for_warnings(heuristic_warnings)
        review_provider = None
        review_model = None
        review_summary = None
        warnings = list(heuristic_warnings)
        risk_level = heuristic_risk

        review = await self._review_script_with_model(
            script=request.script,
            runtime=runtime,
            heuristic_warnings=heuristic_warnings,
        )
        if review is not None:
            review_provider = review["provider"]
            review_model = review["model"]
            review_summary = review["summary"]
            warnings = _dedupe_lines([*review["warnings"], *heuristic_warnings])
            risk_level = max(
                heuristic_risk,
                review["risk_level"],
                key=_risk_level_rank,
            )

        confirmation_id = f"script-{uuid4().hex[:12]}"
        approval_timeout_seconds = max(
            1.0,
            request.approval_timeout_seconds or self._config.script_approval_ttl_seconds,
        )
        summary = (
            review_summary
            or "This script can execute arbitrary code on the current machine. "
            "Review the runtime and warnings before confirming."
        )

        pending = _PendingScriptApproval(
            confirmation_id=confirmation_id,
            script=request.script,
            runtime=runtime,
            shell_label=shell_label,
            timeout_seconds=max(1.0, request.timeout_seconds),
            approval_timeout_seconds=approval_timeout_seconds,
            risk_level=risk_level,
            summary=summary,
            warnings=warnings,
            created_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._pending[confirmation_id] = pending

        preview = request.script.strip().splitlines()
        preview_text = "\n".join(preview[:8]).strip()
        return ScriptExecutionPreviewResponse(
            confirmation_id=confirmation_id,
            runtime=runtime,
            preferred_shell=shell_label,
            risk_level=risk_level,
            review_provider=review_provider,
            review_model=review_model,
            review_summary=review_summary,
            requires_confirmation=True,
            approval_timeout_seconds=approval_timeout_seconds,
            summary=summary,
            warnings=warnings,
            preview=preview_text or request.script.strip()[:400],
            os_name=platform.system() or "Unknown",
        )

    def execute_prepared_script(
        self,
        request: ScriptExecutionRunRequest,
    ) -> ScriptExecutionResponse:
        self._purge_expired()
        if not request.confirm:
            raise ValueError("Script execution requires explicit confirmation.")

        with self._lock:
            pending = self._pending.pop(request.confirmation_id, None)
        if pending is None:
            raise ValueError("The script confirmation has expired or was not found.")

        command = self._command_for_runtime(pending.runtime, pending.script)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=pending.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return ScriptExecutionResponse(
                ok=False,
                confirmation_id=pending.confirmation_id,
                runtime=pending.runtime,
                preferred_shell=pending.shell_label,
                exit_code=None,
                timed_out=True,
                stdout=self._truncate(exc.stdout or ""),
                stderr=self._truncate(exc.stderr or ""),
                summary="Script execution timed out.",
            )

        stdout = self._truncate(completed.stdout or "")
        stderr = self._truncate(completed.stderr or "")
        ok = completed.returncode == 0
        summary = (
            "Script executed successfully."
            if ok
            else f"Script finished with exit code {completed.returncode}."
        )
        return ScriptExecutionResponse(
            ok=ok,
            confirmation_id=pending.confirmation_id,
            runtime=pending.runtime,
            preferred_shell=pending.shell_label,
            exit_code=completed.returncode,
            timed_out=False,
            stdout=stdout,
            stderr=stderr,
            summary=summary,
        )

    async def _review_script_with_model(
        self,
        *,
        script: str,
        runtime: ScriptRuntime,
        heuristic_warnings: list[str],
    ) -> dict | None:
        if self._model_router is None or self._state is None:
            return None

        review_settings = self._script_review_settings()
        try:
            response = await self._model_router.chat(
                ChatRequest(
                    message=_build_script_review_prompt(
                        script=script,
                        runtime=runtime,
                        heuristic_warnings=heuristic_warnings,
                    ),
                    system_prompt=(
                        "You are a strict script security reviewer for desktop agents. "
                        "Return JSON only."
                    ),
                ),
                settings_override=review_settings,
            )
        except Exception as exc:
            self._state.append_event(f"Script review model failed: {exc}")
            return None

        parsed = _parse_script_review_payload(response.content)
        return {
            "provider": response.provider,
            "model": response.model,
            "summary": parsed["summary"],
            "warnings": parsed["warnings"],
            "risk_level": parsed["risk_level"],
        }

    def _script_review_settings(self) -> ProviderSettingsPayload:
        assert self._state is not None
        base = self._state.get_provider_settings()
        automation = self._state.get_automation_settings()
        review = automation.script_review_settings.model_copy(deep=True)
        resolved = self._model_router.resolve_settings(  # type: ignore[union-attr]
            base=base,
            assignment={
                "provider": review.provider.value if review.provider else None,
                "model": review.model,
                "base_url": review.base_url or None,
            },
        )
        return resolved.model_copy(
            update={
                "api_key": review.api_key or resolved.api_key,
                "organization": review.organization or resolved.organization,
                "timeout_seconds": review.timeout_seconds or resolved.timeout_seconds,
                "allow_mock_fallback": review.allow_mock_fallback,
            }
        )

    def _resolve_runtime(self, runtime: ScriptRuntime, script: str) -> ScriptRuntime:
        if runtime != ScriptRuntime.AUTO:
            return runtime
        normalized = script.strip()
        python_markers = ("import ", "from ", "def ", "print(", "class ", "if __name__")
        if any(marker in normalized for marker in python_markers):
            return ScriptRuntime.PYTHON
        return ScriptRuntime.SHELL

    def _preferred_runtime(self) -> ScriptRuntime:
        return ScriptRuntime.PYTHON

    def _preferred_shell_label(self, runtime: ScriptRuntime) -> str:
        if runtime == ScriptRuntime.PYTHON:
            return Path(sys.executable).name
        if platform.system() == "Windows":
            return "powershell"
        if shutil.which("bash"):
            return "bash"
        return "sh"

    def _command_for_runtime(self, runtime: ScriptRuntime, script: str) -> list[str]:
        if runtime == ScriptRuntime.PYTHON:
            return [sys.executable, "-c", script]
        if platform.system() == "Windows":
            return ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
        shell = "bash" if shutil.which("bash") else "sh"
        return [shell, "-lc", script]

    def _script_warnings(self, script: str) -> list[str]:
        normalized = script.lower()
        warnings: list[str] = []
        destructive_patterns = [
            "rm -rf",
            "remove-item",
            "del ",
            "format ",
            "shutdown",
            "reboot",
            "reg delete",
            "mkfs",
        ]
        network_patterns = ["curl ", "wget ", "invoke-webrequest", "irm ", "pip install"]
        credential_patterns = ["export ", "$env:", "setx ", "token", "api_key", "password"]

        if any(pattern in normalized for pattern in destructive_patterns):
            warnings.append("Potentially destructive filesystem or system command detected.")
        if any(pattern in normalized for pattern in network_patterns):
            warnings.append("Network or package-install command detected.")
        if any(pattern in normalized for pattern in credential_patterns):
            warnings.append("The script appears to reference secrets or environment variables.")
        if not warnings:
            warnings.append("No specific destructive pattern detected, but this remains a high-trust action.")
        return warnings

    @staticmethod
    def _risk_level_for_warnings(warnings: list[str]) -> ScriptRiskLevel:
        joined = " ".join(warnings).lower()
        if "destructive" in joined or "secrets" in joined:
            return ScriptRiskLevel.HIGH
        if "network" in joined:
            return ScriptRiskLevel.MEDIUM
        return ScriptRiskLevel.LOW

    def _purge_expired(self) -> None:
        now = datetime.now(timezone.utc)
        with self._lock:
            expired_ids = [
                confirmation_id
                for confirmation_id, pending in self._pending.items()
                if pending.created_at
                + timedelta(seconds=pending.approval_timeout_seconds)
                < now
            ]
            for confirmation_id in expired_ids:
                self._pending.pop(confirmation_id, None)

    def _truncate(self, text: str) -> str:
        if len(text) <= self._config.script_output_limit:
            return text
        return text[: self._config.script_output_limit] + "\n...[truncated]"


def _build_script_review_prompt(
    *,
    script: str,
    runtime: ScriptRuntime,
    heuristic_warnings: list[str],
) -> str:
    return "\n".join(
        [
            f"Runtime: {runtime.value}",
            "Heuristic warnings:",
            *[f"- {item}" for item in heuristic_warnings],
            "",
            "Review the script for destructive behavior, security risks, data exfiltration, privilege misuse, and safer alternatives.",
            'Return JSON only in the shape {"summary":"...","risk_level":"low|medium|high","warnings":["..."]}.',
            "",
            script.strip(),
        ]
    )


def _parse_script_review_payload(content: str) -> dict:
    payload = _extract_json_object(content)
    if payload is None:
        return {
            "summary": content.strip() or "Script review completed.",
            "warnings": [],
            "risk_level": ScriptRiskLevel.MEDIUM,
        }

    warnings = payload.get("warnings")
    normalized_warnings = [
        str(item).strip()
        for item in warnings
        if isinstance(item, str) and item.strip()
    ] if isinstance(warnings, list) else []
    risk_value = str(payload.get("risk_level") or "medium").strip().lower()
    try:
        risk_level = ScriptRiskLevel(risk_value)
    except ValueError:
        risk_level = ScriptRiskLevel.MEDIUM
    return {
        "summary": str(payload.get("summary") or content.strip() or "Script review completed.").strip(),
        "warnings": normalized_warnings,
        "risk_level": risk_level,
    }


def _extract_json_object(content: str) -> dict | None:
    fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    candidates: list[str] = []
    if fenced_match:
        candidates.append(fenced_match.group(1))
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        candidates.append(content[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _dedupe_lines(lines: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for line in lines:
        normalized = line.strip()
        if not normalized or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)
    return deduped


def _risk_level_rank(risk: ScriptRiskLevel) -> int:
    return {
        ScriptRiskLevel.LOW: 0,
        ScriptRiskLevel.MEDIUM: 1,
        ScriptRiskLevel.HIGH: 2,
    }[risk]
