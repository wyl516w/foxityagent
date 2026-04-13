import asyncio

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_studio.api.routes import build_router
from agent_studio.core.config import AppConfig
from agent_studio.core.models import ProviderSettingsPayload, ProviderType
from agent_studio.core.state import SharedState
from agent_studio.services.automation.noop_controller import NoopInputController
from agent_studio.services.automation.permission_manager import PermissionManager
from agent_studio.services.model_router import ModelRouter
from agent_studio.services.providers import ollama_provider, openai_compatible
from agent_studio.services.providers.ollama_provider import OllamaProvider
from agent_studio.services.providers.openai_compatible import OpenAICompatibleProvider


class _FakeAsyncClient:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs) -> httpx.Response:
        return self._response


def test_openai_compatible_health_check_success(monkeypatch) -> None:
    request = httpx.Request("GET", "https://example.test/models")
    response = httpx.Response(
        200,
        request=request,
        json={"data": [{"id": "gpt-4.1-mini"}, {"id": "gpt-4.1"}]},
    )
    monkeypatch.setattr(
        openai_compatible.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(response),
    )

    provider = OpenAICompatibleProvider(config=AppConfig())
    result = asyncio.run(
        provider.health_check(
            ProviderSettingsPayload(
                provider=ProviderType.OPENAI_COMPATIBLE,
                base_url="https://example.test",
                api_key="sk-test",
                model="gpt-4.1-mini",
                timeout_seconds=5.0,
            )
        )
    )

    assert result.ok is True
    assert result.reachable is True
    assert result.authenticated is True
    assert "gpt-4.1-mini" in result.discovered_models


def test_openai_compatible_health_check_unauthorized(monkeypatch) -> None:
    request = httpx.Request("GET", "https://example.test/models")
    response = httpx.Response(401, request=request, json={"error": "unauthorized"})
    monkeypatch.setattr(
        openai_compatible.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(response),
    )

    provider = OpenAICompatibleProvider(config=AppConfig())
    result = asyncio.run(
        provider.health_check(
            ProviderSettingsPayload(
                provider=ProviderType.OPENAI_COMPATIBLE,
                base_url="https://example.test",
                api_key="bad-key",
                model="gpt-4.1-mini",
                timeout_seconds=5.0,
            )
        )
    )

    assert result.ok is False
    assert result.reachable is True
    assert result.authenticated is False
    assert "authentication failed" in result.message.lower()


def test_ollama_health_check_success(monkeypatch) -> None:
    request = httpx.Request("GET", "http://127.0.0.1:11434/api/tags")
    response = httpx.Response(
        200,
        request=request,
        json={"models": [{"name": "qwen2.5:7b-instruct"}, {"name": "llama3.2:3b"}]},
    )
    monkeypatch.setattr(
        ollama_provider.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(response),
    )

    provider = OllamaProvider(config=AppConfig())
    result = asyncio.run(
        provider.health_check(
            ProviderSettingsPayload(
                provider=ProviderType.OLLAMA,
                base_url="http://127.0.0.1:11434",
                model="qwen2.5:7b-instruct",
                timeout_seconds=5.0,
            )
        )
    )

    assert result.ok is True
    assert result.reachable is True
    assert "qwen2.5:7b-instruct" in result.discovered_models


def test_provider_health_route_supports_mock_provider() -> None:
    config = AppConfig()
    state = SharedState(config=config)
    permission_manager = PermissionManager(state=state)
    app = FastAPI()
    app.include_router(
        build_router(
            config=config,
            state=state,
            model_router=ModelRouter(config=config, state=state),
            permission_manager=permission_manager,
            input_controller=NoopInputController(
                state=state,
                permission_manager=permission_manager,
            ),
            conversation_service=None,
            perception_service=None,
        )
    )
    client = TestClient(app)

    response = client.post(
        "/api/provider/health",
        json={
            "provider": "mock",
            "base_url": "",
            "api_key": "",
            "model": "mock",
            "organization": None,
            "timeout_seconds": 5.0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["provider"] == "mock"


def test_provider_capabilities_route_returns_profiles() -> None:
    config = AppConfig()
    state = SharedState(config=config)
    permission_manager = PermissionManager(state=state)
    app = FastAPI()
    app.include_router(
        build_router(
            config=config,
            state=state,
            model_router=ModelRouter(config=config, state=state),
            permission_manager=permission_manager,
            input_controller=NoopInputController(
                state=state,
                permission_manager=permission_manager,
            ),
            conversation_service=None,
            perception_service=None,
        )
    )
    client = TestClient(app)

    response = client.post(
        "/api/provider/capabilities",
        json={
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "api_key": "",
            "model": "qwen2.5:7b-instruct",
            "organization": None,
            "timeout_seconds": 5.0,
            "allow_mock_fallback": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["current_provider"] == "ollama"
    assert payload["allow_mock_fallback"] is False
    assert len(payload["capabilities"]) == 3
    ollama_profile = next(
        profile for profile in payload["capabilities"] if profile["provider"] == "ollama"
    )
    assert ollama_profile["local_runtime"] is True


def test_provider_health_all_route_checks_local_and_remote_routes(monkeypatch) -> None:
    openai_request = httpx.Request("GET", "https://example.test/models")
    openai_response = httpx.Response(
        200,
        request=openai_request,
        json={"data": [{"id": "gpt-4.1-mini"}, {"id": "gpt-4.1"}]},
    )
    ollama_request = httpx.Request("GET", "http://127.0.0.1:11434/api/tags")
    ollama_response = httpx.Response(
        200,
        request=ollama_request,
        json={"models": [{"name": "qwen3-vl:4b"}, {"name": "llama3.2:3b"}]},
    )
    monkeypatch.setattr(
        openai_compatible.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(openai_response),
    )
    monkeypatch.setattr(
        ollama_provider.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(ollama_response),
    )

    config = AppConfig()
    state = SharedState(config=config)
    permission_manager = PermissionManager(state=state)
    app = FastAPI()
    app.include_router(
        build_router(
            config=config,
            state=state,
            model_router=ModelRouter(config=config, state=state),
            permission_manager=permission_manager,
            input_controller=NoopInputController(
                state=state,
                permission_manager=permission_manager,
            ),
            conversation_service=None,
            perception_service=None,
        )
    )
    client = TestClient(app)

    response = client.post(
        "/api/provider/health/all",
        json={
            "provider": "openai_compatible",
            "base_url": "https://example.test",
            "api_key": "sk-test",
            "model": "gpt-4.1-mini",
            "organization": None,
            "timeout_seconds": 5.0,
            "allow_mock_fallback": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["current_provider"] == "openai_compatible"
    assert payload["ok_count"] == 3
    assert payload["reachable_count"] == 3
    assert len(payload["results"]) == 3
    ollama_payload = next(
        item for item in payload["results"] if item["provider"] == "ollama"
    )
    assert ollama_payload["selected_model_available"] is True
    assert "qwen3-vl:4b" in ollama_payload["discovered_models"]
