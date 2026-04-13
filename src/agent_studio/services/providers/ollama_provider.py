from __future__ import annotations

import time

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


class OllamaProvider(BaseProvider):
    async def generate(
        self, request: ChatRequest, settings: ProviderSettingsPayload
    ) -> ChatResponse:
        started = time.perf_counter()
        url = f"{(settings.base_url or self.config.ollama_base_url).rstrip('/')}/api/chat"
        model_name = settings.model or self.config.default_local_model
        if request.attachments and not _is_probably_vision_model(model_name):
            raise ValueError(
                f"Ollama model '{model_name}' does not look vision-capable. "
                "Use a multimodal model such as qwen3-vl:4b."
            )
        payload = {
            "model": model_name,
            "stream": False,
            "messages": self._build_messages(request),
        }

        async with httpx.AsyncClient(timeout=settings.timeout_seconds) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

        message = data.get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content:
            raise ValueError("Ollama response did not contain text content.")

        latency_ms = int((time.perf_counter() - started) * 1000)
        return ChatResponse(
            provider=ProviderType.OLLAMA,
            model=payload["model"],
            content=content,
            used_mock=False,
            vision_used=bool(request.attachments),
            attachment_count=len(request.attachments),
            latency_ms=latency_ms,
        )

    async def health_check(self, settings: ProviderSettingsPayload) -> ProviderHealthResponse:
        started = time.perf_counter()
        base_url = (settings.base_url or self.config.ollama_base_url).rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=settings.timeout_seconds) as client:
                response = await client.get(f"{base_url}/api/tags")
        except httpx.HTTPError as exc:
            return ProviderHealthResponse(
                provider=ProviderType.OLLAMA,
                base_url=base_url,
                model=settings.model or self.config.default_local_model,
                selected_model_available=False,
                ok=False,
                reachable=False,
                authenticated=True,
                message=f"Connection failed: {exc}",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

        if response.status_code >= 400:
            return ProviderHealthResponse(
                provider=ProviderType.OLLAMA,
                base_url=base_url,
                model=settings.model or self.config.default_local_model,
                selected_model_available=False,
                ok=False,
                reachable=True,
                authenticated=True,
                message=f"Ollama reached, but returned HTTP {response.status_code}.",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

        response.raise_for_status()
        data = response.json()
        models = _extract_ollama_models(data)
        latency_ms = int((time.perf_counter() - started) * 1000)
        selected_model_available = not (
            settings.model and models and settings.model not in models
        )
        message = "Ollama reached successfully."
        if not selected_model_available:
            message = "Ollama reached successfully, but the selected model is not downloaded yet."
        return ProviderHealthResponse(
            provider=ProviderType.OLLAMA,
            base_url=base_url,
            model=settings.model or self.config.default_local_model,
            selected_model_available=selected_model_available,
            ok=True,
            reachable=True,
            authenticated=True,
            message=message,
            latency_ms=latency_ms,
            discovered_models=models[:20],
        )

    def describe_capabilities(
        self,
        settings: ProviderSettingsPayload,
    ) -> ProviderCapabilityProfile:
        return ProviderCapabilityProfile(
            provider=ProviderType.OLLAMA,
            label="Ollama",
            supports_text=True,
            supports_vision=_is_probably_vision_model(
                settings.model or self.config.default_local_model
            ),
            supports_tools=False,
            supports_model_listing=True,
            local_runtime=True,
            remote_runtime=False,
            default_model=settings.model or self.config.default_local_model,
            routing_hint=(
                "Good for local-first execution when you want on-device inference and downloaded models."
            ),
        )

    def _build_messages(self, request: ChatRequest) -> list[dict]:
        messages: list[dict] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})

        user_message = {
            "role": "user",
            "content": request.message.strip() or "Describe the provided image.",
        }
        if request.attachments:
            user_message["images"] = [
                self.attachment_to_base64(attachment)[0]
                for attachment in request.attachments
            ]
        messages.append(user_message)
        return messages


def _extract_ollama_models(payload: dict) -> list[str]:
    models = payload.get("models")
    if not isinstance(models, list):
        return []
    names: list[str] = []
    for item in models:
        if isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str) and name:
                names.append(name)
    return names


def _is_probably_vision_model(model_name: str) -> bool:
    normalized = model_name.lower().replace("_", "-")
    markers = (
        "qwen3-vl",
        "qwen2.5-vl",
        "minicpm-v",
        "llava",
        "vision",
        "-vl",
        "vl:",
    )
    return any(marker in normalized for marker in markers)
