from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="AGENT_",
        extra="ignore",
    )

    app_name: str = "Agent Studio"
    backend_host: str = "127.0.0.1"
    backend_port: int = 8765
    openai_base_url: str = "https://api.openai.com/v1"
    ollama_base_url: str = "http://127.0.0.1:11434"
    default_remote_model: str = "gpt-4.1-mini"
    default_local_model: str = "qwen3-vl:4b"
    request_timeout_seconds: float = 60.0
    ui_poll_interval_ms: int = 4000
    database_path: Path = Path("data/agent_studio.db")
    captures_dir: Path = Path("data/captures")
    recent_event_limit: int = 50
    event_retention_limit: int = 1000
    script_output_limit: int = 12000
    script_approval_ttl_seconds: int = 60

    @property
    def backend_url(self) -> str:
        return f"http://{self.backend_host}:{self.backend_port}"
