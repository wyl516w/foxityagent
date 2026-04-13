from __future__ import annotations

import time

from agent_studio.core.models import (
    ChatRequest,
    ChatResponse,
    ProviderCapabilityProfile,
    ProviderHealthResponse,
    ProviderSettingsPayload,
    ProviderType,
)
from agent_studio.services.providers.base import BaseProvider


class MockProvider(BaseProvider):
    async def generate(
        self, request: ChatRequest, settings: ProviderSettingsPayload
    ) -> ChatResponse:
        started = time.perf_counter()
        attachment_count = len(request.attachments)
        content = (
            "Mock provider is active.\n\n"
            f"Your message was:\n{request.message}\n\n"
            "Next step:\n"
            "- Switch to OpenAI-compatible mode and add an API key, or\n"
            "- Switch to Ollama mode and point the app at a local model server.\n\n"
            "The provider abstraction and desktop/backend wiring are already in place."
        )
        if attachment_count:
            content += (
                f"\n\nThe request also included {attachment_count} image attachment(s), "
                "but the mock provider does not perform real visual analysis."
            )
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ChatResponse(
            provider=ProviderType.MOCK,
            model=settings.model or "mock",
            content=content,
            used_mock=True,
            vision_used=False,
            attachment_count=attachment_count,
            latency_ms=latency_ms,
        )

    async def health_check(self, settings: ProviderSettingsPayload) -> ProviderHealthResponse:
        return ProviderHealthResponse(
            provider=ProviderType.MOCK,
            base_url="mock://local",
            model=settings.model or "mock",
            selected_model_available=True,
            ok=True,
            reachable=True,
            authenticated=True,
            message="Mock provider is always available for local development.",
            latency_ms=0,
            discovered_models=[settings.model or "mock"],
        )

    def describe_capabilities(
        self,
        settings: ProviderSettingsPayload,
    ) -> ProviderCapabilityProfile:
        return ProviderCapabilityProfile(
            provider=ProviderType.MOCK,
            label="Mock",
            supports_text=True,
            supports_vision=False,
            supports_tools=False,
            supports_model_listing=True,
            local_runtime=True,
            remote_runtime=False,
            default_model=settings.model or "mock",
            routing_hint="Safe development fallback that always returns a local mock response.",
        )
