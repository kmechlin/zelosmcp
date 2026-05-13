"""Protocol (interface) for the auth token store."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AuthStoreProtocol(Protocol):
    """Async read/write surface for the encrypted auth token store."""

    async def open(self) -> None: ...
    async def close(self) -> None: ...

    async def put_token(
        self,
        *,
        user_key: str,
        provider: str,
        audience: str | None,
        access_token: str,
        refresh_token: str | None,
        expires_at: float | None,
        scopes: tuple[str, ...] | None,
        identity_username: str | None,
        identity_avatar_url: str | None,
    ) -> None: ...

    async def get_token(
        self,
        *,
        user_key: str,
        provider: str,
        audience: str | None,
    ) -> dict[str, Any] | None: ...

    async def delete_token(
        self,
        *,
        user_key: str,
        provider: str,
        audience: str | None,
    ) -> bool: ...

    async def delete_provider_tokens(self, provider: str) -> int: ...

    async def put_device_session(
        self,
        *,
        session_id: str,
        user_key: str,
        provider: str,
        device_code: str,
        poll_interval: float,
        expires_at: float,
    ) -> None: ...

    async def get_device_session(
        self, session_id: str
    ) -> dict[str, Any] | None: ...

    async def update_device_session(
        self,
        *,
        session_id: str,
        state: str,
        identity_json: str | None = None,
        error_message: str | None = None,
    ) -> None: ...

    async def delete_device_session(self, session_id: str) -> None: ...
    async def prune_expired_device_sessions(self) -> int: ...
