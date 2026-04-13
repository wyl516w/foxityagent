import asyncio

from agent_studio.core.config import AppConfig
from agent_studio.core.models import (
    ChatRequest,
    ChatResponse,
    ProviderCapabilityProfile,
    ProviderHealthResponse,
    ProviderSettingsPayload,
    ProviderType,
)
from agent_studio.core.state import SharedState
from agent_studio.services.model_router import ModelRouter
from agent_studio.services.providers.base import BaseProvider


class _FailingProvider(BaseProvider):
    async def generate(
        self,
        request: ChatRequest,
        settings: ProviderSettingsPayload,
    ) -> ChatResponse:
        raise RuntimeError("primary provider offline")

    async def health_check(
        self,
        settings: ProviderSettingsPayload,
    ) -> ProviderHealthResponse:
        return ProviderHealthResponse(
            provider=settings.provider,
            base_url=settings.base_url,
            model=settings.model,
            ok=False,
            reachable=False,
            authenticated=False,
            message="offline",
        )

    def describe_capabilities(
        self,
        settings: ProviderSettingsPayload,
    ) -> ProviderCapabilityProfile:
        return ProviderCapabilityProfile(
            provider=settings.provider,
            label="Failing",
            supports_text=True,
            default_model=settings.model,
            routing_hint="Fails for fallback testing.",
        )


class _StubMockProvider(BaseProvider):
    async def generate(
        self,
        request: ChatRequest,
        settings: ProviderSettingsPayload,
    ) -> ChatResponse:
        return ChatResponse(
            provider=ProviderType.MOCK,
            model="mock",
            content=f"fallback for: {request.message}",
            used_mock=True,
        )

    async def health_check(
        self,
        settings: ProviderSettingsPayload,
    ) -> ProviderHealthResponse:
        return ProviderHealthResponse(
            provider=ProviderType.MOCK,
            base_url="mock://local",
            model="mock",
            ok=True,
            reachable=True,
            authenticated=True,
            message="ok",
        )

    def describe_capabilities(
        self,
        settings: ProviderSettingsPayload,
    ) -> ProviderCapabilityProfile:
        return ProviderCapabilityProfile(
            provider=ProviderType.MOCK,
            label="Mock",
            supports_text=True,
            local_runtime=True,
            default_model="mock",
            routing_hint="Always available fallback.",
        )


def test_model_router_falls_back_to_mock_when_primary_provider_fails() -> None:
    config = AppConfig()
    state = SharedState(config=config)
    state.update_provider_settings(
        ProviderSettingsPayload(
            provider=ProviderType.OPENAI_COMPATIBLE,
            base_url="https://example.test/v1",
            api_key="sk-test",
            model="gpt-4.1-mini",
            allow_mock_fallback=True,
        )
    )
    router = ModelRouter(config=config, state=state)
    router._providers[ProviderType.OPENAI_COMPATIBLE] = _FailingProvider(config=config)
    router._providers[ProviderType.MOCK] = _StubMockProvider(config=config)

    response = asyncio.run(router.chat(ChatRequest(message="hello fallback")))

    assert response.provider == ProviderType.MOCK
    assert response.model == "mock"
    assert response.used_mock is True
    assert response.fallback_used is True
    assert response.attempted_providers == [
        ProviderType.OPENAI_COMPATIBLE,
        ProviderType.MOCK,
    ]
    assert response.fallback_reason == "primary provider offline"


def test_model_router_describes_provider_capabilities() -> None:
    config = AppConfig()
    state = SharedState(config=config)
    state.update_provider_settings(
        ProviderSettingsPayload(
            provider=ProviderType.OLLAMA,
            base_url="http://127.0.0.1:11434",
            model="qwen2.5:7b-instruct",
            allow_mock_fallback=False,
        )
    )
    router = ModelRouter(config=config, state=state)

    capabilities = router.describe_capabilities()

    assert capabilities.current_provider == ProviderType.OLLAMA
    assert capabilities.current_model == "qwen2.5:7b-instruct"
    assert capabilities.allow_mock_fallback is False
    assert len(capabilities.capabilities) == 3
    assert any(profile.provider == ProviderType.OLLAMA for profile in capabilities.capabilities)
    assert any(
        profile.provider == ProviderType.OPENAI_COMPATIBLE
        for profile in capabilities.capabilities
    )


def test_model_router_resolves_agent_assignment_to_local_model() -> None:
    config = AppConfig()
    state = SharedState(config=config)
    state.update_provider_settings(
        ProviderSettingsPayload(
            provider=ProviderType.OPENAI_COMPATIBLE,
            base_url="https://example.test/v1",
            api_key="sk-test",
            model="gpt-4.1-mini",
            allow_mock_fallback=False,
        )
    )
    router = ModelRouter(config=config, state=state)

    resolved = router.resolve_settings(
        assignment={
            "provider": ProviderType.OLLAMA.value,
            "model": "qwen3-vl:4b",
        }
    )

    assert resolved.provider == ProviderType.OLLAMA
    assert resolved.base_url == config.ollama_base_url
    assert resolved.model == "qwen3-vl:4b"
    assert resolved.api_key == ""
