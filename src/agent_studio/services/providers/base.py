from __future__ import annotations

from abc import ABC, abstractmethod
import base64
import mimetypes
from pathlib import Path

from agent_studio.core.config import AppConfig
from agent_studio.core.models import (
    ChatImageAttachment,
    ChatRequest,
    ChatResponse,
    ProviderCapabilityProfile,
    ProviderHealthResponse,
    ProviderSettingsPayload,
)


class BaseProvider(ABC):
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @abstractmethod
    async def generate(
        self, request: ChatRequest, settings: ProviderSettingsPayload
    ) -> ChatResponse:
        raise NotImplementedError

    @abstractmethod
    async def health_check(self, settings: ProviderSettingsPayload) -> ProviderHealthResponse:
        raise NotImplementedError

    @abstractmethod
    def describe_capabilities(
        self,
        settings: ProviderSettingsPayload,
    ) -> ProviderCapabilityProfile:
        raise NotImplementedError

    @staticmethod
    def normalize_attachment_name(attachment: ChatImageAttachment) -> str:
        if attachment.name:
            return attachment.name
        if attachment.image_path:
            return Path(attachment.image_path).name
        return "image"

    @classmethod
    def attachment_to_base64(
        cls,
        attachment: ChatImageAttachment,
    ) -> tuple[str, str]:
        media_type = attachment.media_type or "image/png"
        if attachment.image_base64:
            payload = attachment.image_base64.strip()
            if payload.startswith("data:") and "," in payload:
                header, payload = payload.split(",", 1)
                media_type = header[5:].split(";")[0] or media_type
            return payload, media_type

        if attachment.image_path:
            path = Path(attachment.image_path).expanduser()
            if not path.exists():
                raise ValueError(f"Image attachment was not found: {path}")
            if path.is_dir():
                raise ValueError(f"Image attachment must be a file: {path}")
            media_type = (
                attachment.media_type
                or mimetypes.guess_type(path.name)[0]
                or "application/octet-stream"
            )
            payload = base64.b64encode(path.read_bytes()).decode("ascii")
            return payload, media_type

        raise ValueError("Image attachment requires image_path or image_base64.")

    @classmethod
    def attachment_to_data_uri(cls, attachment: ChatImageAttachment) -> str:
        payload, media_type = cls.attachment_to_base64(attachment)
        return f"data:{media_type};base64,{payload}"
