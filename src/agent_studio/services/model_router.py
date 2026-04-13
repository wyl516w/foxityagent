from __future__ import annotations

import asyncio

from agent_studio.core.config import AppConfig
from agent_studio.core.models import (
    AgentModelAssignment,
    ChatRequest,
    ChatResponse,
    ProviderCapabilitiesResponse,
    ProviderHealthResponse,
    ProviderHealthSweepResponse,
    ProviderSettingsPayload,
    ProviderType,
)
from agent_studio.core.state import SharedState
from agent_studio.services.providers.base import BaseProvider
from agent_studio.services.providers.mock_provider import MockProvider
from agent_studio.services.providers.ollama_provider import OllamaProvider
from agent_studio.services.providers.openai_compatible import OpenAICompatibleProvider


class ModelRouter:
    def __init__(self, config: AppConfig, state: SharedState) -> None:
        self._config = config
        self._state = state
        self._providers: dict[ProviderType, BaseProvider] = {
            ProviderType.MOCK: MockProvider(config=config),
            ProviderType.OPENAI_COMPATIBLE: OpenAICompatibleProvider(config=config),
            ProviderType.OLLAMA: OllamaProvider(config=config),
        }

    async def chat(
        self,
        request: ChatRequest,
        *,
        settings_override: ProviderSettingsPayload | None = None,
        assignment: AgentModelAssignment | dict | None = None,
    ) -> ChatResponse:
        provider_settings = (
            settings_override.model_copy(deep=True)
            if settings_override is not None
            else self.resolve_settings(assignment=assignment)
        )
        attempted: list[ProviderType] = []

        primary_provider = self._providers[provider_settings.provider]
        attempted.append(provider_settings.provider)
        try:
            response = await primary_provider.generate(
                request=request,
                settings=provider_settings,
            )
            return response.model_copy(update={"attempted_providers": attempted})
        except Exception as exc:
            if (
                not provider_settings.allow_mock_fallback
                or provider_settings.provider == ProviderType.MOCK
            ):
                raise

            mock_settings = provider_settings.model_copy(
                update={
                    "provider": ProviderType.MOCK,
                    "base_url": "mock://local",
                    "model": "mock",
                    "organization": None,
                    "api_key": "",
                }
            )
            attempted.append(ProviderType.MOCK)
            fallback_response = await self._providers[ProviderType.MOCK].generate(
                request=request,
                settings=mock_settings,
            )
            return fallback_response.model_copy(
                update={
                    "fallback_used": True,
                    "fallback_reason": str(exc),
                    "attempted_providers": attempted,
                }
            )

    async def check_provider(
        self,
        settings: ProviderSettingsPayload | None = None,
    ) -> ProviderHealthResponse:
        provider_settings = settings or self._state.get_provider_settings()
        provider = self._providers[provider_settings.provider]
        return await provider.health_check(settings=provider_settings)

    async def check_all_providers(
        self,
        settings: ProviderSettingsPayload | None = None,
    ) -> ProviderHealthSweepResponse:
        provider_settings = settings or self._state.get_provider_settings()
        resolved_settings = [
            self._settings_for_provider(provider_type, provider_settings)
            for provider_type in self._providers
        ]
        results = await asyncio.gather(
            *[self._safe_check_provider(item) for item in resolved_settings]
        )
        return ProviderHealthSweepResponse(
            current_provider=provider_settings.provider,
            current_model=provider_settings.model,
            ok_count=sum(1 for item in results if item.ok and item.selected_model_available),
            reachable_count=sum(1 for item in results if item.reachable),
            results=results,
        )

    def describe_capabilities(
        self,
        settings: ProviderSettingsPayload | None = None,
    ) -> ProviderCapabilitiesResponse:
        provider_settings = settings or self._state.get_provider_settings()
        capabilities = [
            provider.describe_capabilities(
                self._settings_for_provider(provider_type, provider_settings)
            )
            for provider_type, provider in self._providers.items()
        ]
        return ProviderCapabilitiesResponse(
            current_provider=provider_settings.provider,
            current_model=provider_settings.model,
            allow_mock_fallback=provider_settings.allow_mock_fallback,
            capabilities=capabilities,
        )

    def resolve_settings(
        self,
        *,
        base: ProviderSettingsPayload | None = None,
        assignment: AgentModelAssignment | dict | None = None,
    ) -> ProviderSettingsPayload:
        provider_settings = (base or self._state.get_provider_settings()).model_copy(deep=True)
        normalized_assignment = self._normalize_assignment(assignment)
        if normalized_assignment is None:
            return provider_settings

        if (
            normalized_assignment.provider is not None
            and normalized_assignment.provider != provider_settings.provider
        ):
            provider_settings = self._settings_for_provider(
                normalized_assignment.provider,
                provider_settings,
            )

        updates: dict[str, object] = {}
        if normalized_assignment.base_url:
            updates["base_url"] = normalized_assignment.base_url
        if normalized_assignment.model:
            updates["model"] = normalized_assignment.model
        if updates:
            provider_settings = provider_settings.model_copy(update=updates)
        return provider_settings

    async def _safe_check_provider(
        self,
        settings: ProviderSettingsPayload,
    ) -> ProviderHealthResponse:
        try:
            return await self._providers[settings.provider].health_check(settings=settings)
        except Exception as exc:  # pragma: no cover - surfaced through API/UI
            return ProviderHealthResponse(
                provider=settings.provider,
                base_url=settings.base_url,
                model=settings.model,
                selected_model_available=False,
                ok=False,
                reachable=False,
                authenticated=bool(settings.api_key) or settings.provider != ProviderType.OPENAI_COMPATIBLE,
                message=f"Health check failed: {exc}",
            )

    @staticmethod
    def _normalize_assignment(
        assignment: AgentModelAssignment | dict | None,
    ) -> AgentModelAssignment | None:
        if assignment is None:
            return None
        if isinstance(assignment, AgentModelAssignment):
            return assignment
        if isinstance(assignment, dict) and any(
            assignment.get(key) for key in ("provider", "model", "base_url")
        ):
            return AgentModelAssignment.model_validate(assignment)
        return None

    def _settings_for_provider(
        self,
        provider_type: ProviderType,
        current: ProviderSettingsPayload,
    ) -> ProviderSettingsPayload:
        if provider_type == current.provider:
            return current
        if provider_type == ProviderType.OPENAI_COMPATIBLE:
            return current.model_copy(
                update={
                    "provider": provider_type,
                    "base_url": self._config.openai_base_url,
                    "model": self._config.default_remote_model,
                }
            )
        if provider_type == ProviderType.OLLAMA:
            return current.model_copy(
                update={
                    "provider": provider_type,
                    "base_url": self._config.ollama_base_url,
                    "model": self._config.default_local_model,
                    "api_key": "",
                    "organization": None,
                }
            )
        return current.model_copy(
            update={
                "provider": ProviderType.MOCK,
                "base_url": "mock://local",
                "model": "mock",
                "api_key": "",
                "organization": None,
            }
        )
