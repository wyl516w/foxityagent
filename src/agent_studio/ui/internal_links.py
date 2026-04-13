from __future__ import annotations

from urllib.parse import quote, unquote


TASK_LINK_SCHEME = "task"
MESSAGE_LINK_SCHEME = "message"
_SUPPORTED_SCHEMES = {TASK_LINK_SCHEME, MESSAGE_LINK_SCHEME}


def build_internal_link(scheme: str, identifier: str) -> str:
    normalized_scheme = scheme.strip().lower()
    normalized_identifier = identifier.strip()
    if normalized_scheme not in _SUPPORTED_SCHEMES:
        raise ValueError(f"Unsupported internal link scheme: {scheme}")
    if not normalized_identifier:
        raise ValueError("Internal links require a non-empty identifier.")
    return f"{normalized_scheme}://{quote(normalized_identifier, safe='')}"


def parse_internal_link(value: str) -> tuple[str, str] | None:
    raw = value.strip()
    if "://" not in raw:
        return None
    scheme, remainder = raw.split("://", 1)
    normalized_scheme = scheme.strip().lower()
    if normalized_scheme not in _SUPPORTED_SCHEMES:
        return None
    identifier = remainder.split("?", 1)[0].strip("/")
    if not identifier:
        return None
    return normalized_scheme, unquote(identifier)


def message_anchor_name(message_id: str) -> str:
    normalized = message_id.strip()
    return f"message-{normalized}" if normalized else "message-"
