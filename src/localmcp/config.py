"""Cursor-compatible MCP config parsing.

Accepts the same ``mcpServers`` shape Cursor uses in ``mcp.json`` and adds an
optional top-level ``primaryMCP`` field naming the server that should also be
mounted at ``/mcp`` (in addition to its always-present ``/<name>/mcp``).

A single ``parse_config`` call returns a list of normalized :class:`ServerSpec`
objects plus the resolved primary name (if any). Reserved path segments are
rejected up front so configs cannot collide with the app's own routes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


RESERVED_NAMES: frozenset[str] = frozenset({
    "api",
    "mcp",
    "docs",
    "redoc",
    "openapi.json",
    "openapi",
    "static",
})

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-.]*$")


class ConfigError(ValueError):
    """Raised when a config payload cannot be parsed into ServerSpecs."""


@dataclass
class ServerSpec:
    """Normalized, transport-tagged description of one MCP backend."""

    name: str
    transport: str  # "stdio" | "sse" | "http"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] | None = None

    def to_status(self) -> dict[str, Any]:
        """Compact JSON-serializable view used by status endpoints."""
        info: dict[str, Any] = {"name": self.name, "transport": self.transport}
        if self.command:
            info["command"] = self.command
            if self.args:
                info["args"] = list(self.args)
            if self.env:
                info["env"] = dict(self.env)
            if self.cwd:
                info["cwd"] = self.cwd
        if self.url:
            info["url"] = self.url
            if self.headers:
                info["headers"] = dict(self.headers)
        return info


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not name:
        raise ConfigError("Server names must be non-empty strings")
    if name.lower() in RESERVED_NAMES:
        raise ConfigError(
            f"Server name '{name}' is reserved (collides with a built-in route)"
        )
    if not _NAME_RE.match(name):
        raise ConfigError(
            f"Server name '{name}' is invalid: use letters, digits, '-', '_', or '.'"
        )


def _coerce_str_dict(value: Any, field_name: str, server_name: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ConfigError(
            f"Server '{server_name}': '{field_name}' must be an object of strings"
        )
    out: dict[str, str] = {}
    for k, v in value.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ConfigError(
                f"Server '{server_name}': '{field_name}' entries must be string→string"
            )
        out[k] = v
    return out


def _parse_server(name: str, raw: Any) -> ServerSpec:
    if not isinstance(raw, dict):
        raise ConfigError(f"Server '{name}': entry must be an object")

    declared_type = raw.get("type")

    # Stdio: presence of `command` (matches Cursor's discrimination rule).
    if "command" in raw:
        command = raw.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ConfigError(f"Server '{name}': 'command' must be a non-empty string")

        args_raw = raw.get("args", [])
        if not isinstance(args_raw, list) or not all(isinstance(a, str) for a in args_raw):
            raise ConfigError(f"Server '{name}': 'args' must be an array of strings")

        env = None
        if "env" in raw and raw["env"] is not None:
            env = _coerce_str_dict(raw["env"], "env", name)

        cwd = raw.get("cwd")
        if cwd is not None and not isinstance(cwd, str):
            raise ConfigError(f"Server '{name}': 'cwd' must be a string")

        return ServerSpec(
            name=name,
            transport="stdio",
            command=command.strip(),
            args=list(args_raw),
            env=env,
            cwd=cwd,
        )

    # Remote transports: discriminated by `type`.
    if declared_type in ("sse", "streamable-http"):
        url = raw.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ConfigError(
                f"Server '{name}': '{declared_type}' config requires a 'url' string"
            )

        headers = None
        if "headers" in raw and raw["headers"] is not None:
            headers = _coerce_str_dict(raw["headers"], "headers", name)

        transport = "sse" if declared_type == "sse" else "http"
        return ServerSpec(
            name=name,
            transport=transport,
            url=url.strip(),
            headers=headers,
        )

    raise ConfigError(
        f"Server '{name}': could not determine transport. "
        "Provide 'command' (stdio) or 'type' set to 'sse' or 'streamable-http'."
    )


def parse_config(raw: Any) -> tuple[list[ServerSpec], str | None]:
    """Parse a Cursor-style ``mcpServers`` payload.

    Args:
        raw: Decoded JSON object. Must contain ``mcpServers`` mapping
            name → server config. May also contain ``primaryMCP``.

    Returns:
        ``(specs, primary_name)`` — order of ``specs`` matches insertion order
        of ``mcpServers``. ``primary_name`` is ``None`` if not specified.

    Raises:
        ConfigError: On any structural or value error.
    """
    if not isinstance(raw, dict):
        raise ConfigError("Config must be a JSON object")

    servers = raw.get("mcpServers")
    if servers is None:
        raise ConfigError("Config must contain 'mcpServers'")
    if not isinstance(servers, dict):
        raise ConfigError("'mcpServers' must be an object mapping name → server config")
    if not servers:
        raise ConfigError("'mcpServers' must contain at least one server")

    specs: list[ServerSpec] = []
    seen: set[str] = set()
    for name, entry in servers.items():
        _validate_name(name)
        lower = name.lower()
        if lower in seen:
            raise ConfigError(f"Duplicate server name (case-insensitive): '{name}'")
        seen.add(lower)
        specs.append(_parse_server(name, entry))

    primary = raw.get("primaryMCP")
    if primary is not None and not isinstance(primary, str):
        raise ConfigError("'primaryMCP' must be a string")
    # NOTE: primaryMCP is deprecated as of v0.3 — `/mcp` always aggregates every
    # running server. We still parse the field so old pasted configs keep working;
    # ProxyManager.start_all logs a deprecation warning when it sees a value.

    return specs, primary
