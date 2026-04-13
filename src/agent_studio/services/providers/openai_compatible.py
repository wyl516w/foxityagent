from __future__ import annotations

import time
from typing import Any

import httpx

from agent_studio.core.models import (
    ChatRequest,
    ChatResponse,
    ProviderCapabilityProfile,
    ProviderHealthResponse,
    ProviderSettingsPayload,
    ProviderType,
)
from agent_studio.services.providers.base import BaseProvider


class OpenAICompatibleProvider(BaseProvider):
    async def generate(
        self, request: ChatRequest, settings: ProviderSettingsPayload
    ) -> ChatResponse:
        started = time.perf_counter()
        url = f"{(settings.base_url or self.config.openai_base_url).rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if settings.api_key:
            headers["Authorization"] = f"Bearer {settings.api_key}"
        if settings.organization:
            headers["OpenAI-Organization"] = settings.organization

        payload = {
            "model": settings.model or self.config.default_remote_model,
            "stream": False,
            "temperature": 0.3,
            "messages": self._build_messages(request),
        }

        async with httpx.AsyncClient(timeout=settings.timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        content = _extract_openai_text(data)
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ChatResponse(
            provider=ProviderType.OPENAI_COMPATIBLE,
            model=payload["model"],
            content=content,
            used_mock=False,
            vision_used=bool(request.attachments),
            attachment_count=len(request.attachments),
            latency_ms=latency_ms,
        )

    async def health_check(self, settings: ProviderSettingsPayload) -> ProviderHealthResponse:
        started = time.perf_counter()
        base_url = (settings.base_url or self.config.openai_base_url).rstrip("/")
        headers = {"Content-Type": "application/json"}
        if settings.api_key:
            headers["Authorization"] = f"Bearer {settings.api_key}"
        if settings.organization:
            headers["OpenAI-Organization"] = settings.organization

        try:
            async with httpx.AsyncClient(timeout=settings.timeout_seconds) as client:
                response = await client.get(f"{base_url}/models", headers=headers)
        except httpx.HTTPError as exc:
            return ProviderHealthResponse(
                provider=ProviderType.OPENAI_COMPATIBLE,
                base_url=base_url,
                model=settings.model or self.config.default_remote_model,
                selected_model_available=False,
                ok=False,
                reachable=False,
                authenticated=bool(settings.api_key),
                message=f"Connection failed: {exc}",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code == 401:
            return ProviderHealthResponse(
                provider=ProviderType.OPENAI_COMPATIBLE,
                base_url=base_url,
                model=settings.model or self.config.default_remote_model,
                selected_model_available=False,
                ok=False,
                reachable=True,
                authenticated=False,
                message="Provider reached, but authentication failed. Check API key or org.",
                latency_ms=latency_ms,
            )

        if response.status_code >= 400:
            return ProviderHealthResponse(
                provider=ProviderType.OPENAI_COMPATIBLE,
                base_url=base_url,
                model=settings.model or self.config.default_remote_model,
                selected_model_available=False,
                ok=False,
                reachable=True,
                authenticated=bool(settings.api_key),
                message=f"Provider reached, but returned HTTP {response.status_code}.",
                latency_ms=latency_ms,
            )

        response.raise_for_status()
        data = response.json()
        model_ids = _extract_openai_models(data)
        selected_model_available = not (
            settings.model and model_ids and settings.model not in model_ids
        )
        message = "Provider reached successfully."
        if not selected_model_available:
            message = (
                "Provider reached successfully, but the selected model was not found in /models."
            )
        return ProviderHealthResponse(
            provider=ProviderType.OPENAI_COMPATIBLE,
            base_url=base_url,
            model=settings.model or self.config.default_remote_model,
            selected_model_available=selected_model_available,
            ok=True,
            reachable=True,
            authenticated=True if settings.api_key else response.status_code < 400,
            message=message,
            latency_ms=latency_ms,
            discovered_models=model_ids[:20],
        )

    def describe_capabilities(
        self,
        settings: ProviderSettingsPayload,
    ) -> ProviderCapabilityProfile:
        return ProviderCapabilityProfile(
            provider=ProviderType.OPENAI_COMPATIBLE,
            label="OpenAI Compatible",
            supports_text=True,
            supports_vision=True,
            supports_tools=True,
            supports_model_listing=True,
            local_runtime=False,
            remote_runtime=True,
            default_model=settings.model or self.config.default_remote_model,
            routing_hint=(
                "Best when you have a compatible remote endpoint and want broad model and tool support."
            ),
        )

    def _build_messages(self, request: ChatRequest) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})

        if not request.attachments:
            messages.append({"role": "user", "content": request.message})
            return messages

        content: list[dict[str, Any]] = []
        prompt = request.message.strip() or "Describe the provided image."
        content.append({"type": "text", "text": prompt})
        for attachment in request.attachments:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": self.attachment_to_data_uri(attachment)},
                }
            )
        messages.append({"role": "user", "content": content})
        return messages


def _extract_openai_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        raise ValueError("OpenAI-compatible response did not contain any choices.")

    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                if text:
                    parts.append(text)
        if parts:
            return "\n".join(parts)

    raise ValueError("OpenAI-compatible response content could not be parsed.")


def _extract_openai_models(payload: dict[str, Any]) -> list[str]:
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    model_ids: list[str] = []
    for item in data:
        if isinstance(item, dict):
            model_id = item.get("id")
            if isinstance(model_id, str) and model_id:
                model_ids.append(model_id)
    return model_ids
