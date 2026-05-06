"""Cursor-compatible MCP config parsing.

Accepts the same ``mcpServers`` shape Cursor uses in ``mcp.json`` and adds an
optional top-level ``primaryMCP`` field naming the server that should also be
mounted at ``/mcp`` (in addition to its always-present ``/<name>/mcp``).

A single ``parse_config`` call returns a list of normalized :class:`ServerSpec`
objects plus the resolved primary name (if any). Reserved path segments are
rejected up front so configs cannot collide with the app's own routes.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


RESERVED_NAMES: frozenset[str] = frozenset({
    "api",
    "mcp",
    "docs",
    "redoc",
    "openapi.json",
    "openapi",
    "static",
    # `localmcp` is the always-on built-in backend (BuiltinServer in
    # localmcp.builtin); reserved so user configs can't shadow it.
    "localmcp",
})

# Path prefixes a backend's reverseProxy.mount cannot claim. These either
# host LocalMCP's own surface (/api/*, /docs, /redoc, /openapi.json,
# /catalog) or are reserved by the MCP dispatcher (/, /mcp). Anything not
# in this set is fair game.
RESERVED_MOUNTS: frozenset[str] = frozenset({
    "/",
    "/api",
    "/mcp",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/catalog",
})

# Compression knobs for tool-list shrinking. See docs/compression.md.
COMPRESS_LEVELS: frozenset[str] = frozenset({"low", "medium", "high", "max"})
COMPRESS_SCOPES: frozenset[str] = frozenset({"catalog", "aggregator", "global"})

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-.]*$")
_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(ValueError):
    """Raised when a config payload cannot be parsed into ServerSpecs."""


@dataclass
class ReverseProxySpec:
    """HTTP reverse-proxy configuration for one backend.

    When set, LocalMCP forwards HTTP requests on ``mount`` to ``upstream``,
    injecting a canonical ``X-Forwarded-*`` header set so the upstream knows
    its public-facing path. Lets you expose a backend's HTTP sidecar (e.g.
    pincher's dashboard) through LocalMCP's port without leaking the
    backend's own port.
    """

    mount: str
    upstream: str
    strip_prefix: bool = False
    headers: dict[str, str] = field(default_factory=dict)
    auth_bearer: str | None = None

    def to_status(self) -> dict[str, Any]:
        """JSON-serializable view round-tripping the input config shape.

        ``auth.bearer`` is intentionally omitted from the status payload so
        secrets don't leak through ``/api/status``.
        """
        info: dict[str, Any] = {"mount": self.mount, "upstream": self.upstream}
        if self.strip_prefix:
            info["stripPrefix"] = True
        if self.headers:
            info["headers"] = dict(self.headers)
        if self.auth_bearer:
            info["auth"] = {"bearer": "***"}
        return info


@dataclass
class CompressSpec:
    """Tool-list compression policy for one backend.

    Replaces the backend's full tool surface with a two-tool wrapper pair
    (``get_tool_schema`` + ``invoke_tool``, or a single ``list_tools`` at
    level=max) so the LLM sees a much smaller schema in ``tools/list``.

    - ``level`` controls how aggressively the inlined catalog is summarised.
    - ``scope`` controls which endpoints the wrapper replacement applies to:
      ``catalog`` (docs/discovery only), ``aggregator`` (default — replaces
      tools at ``/mcp``), or ``global`` (also replaces at ``/<name>/mcp``).
    """

    level: str = "medium"
    scope: str = "aggregator"

    def to_status(self) -> dict[str, Any]:
        return {"level": self.level, "scope": self.scope}


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
    reverse_proxy: ReverseProxySpec | None = None
    compress: CompressSpec | None = None

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
        if self.reverse_proxy is not None:
            info["reverseProxy"] = self.reverse_proxy.to_status()
        if self.compress is not None:
            info["compress"] = self.compress.to_status()
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


def _validate_mount(server_name: str, mount: Any) -> str:
    """Normalize and validate a reverse-proxy mount path.

    Returns the canonical form (leading slash, no trailing slash). Rejects
    values that would shadow LocalMCP's own routes or the MCP dispatcher.
    """
    if not isinstance(mount, str) or not mount:
        raise ConfigError(
            f"Server '{server_name}': reverseProxy.mount must be a non-empty string"
        )
    if not mount.startswith("/"):
        raise ConfigError(
            f"Server '{server_name}': reverseProxy.mount must start with '/' (got {mount!r})"
        )
    if any(ch.isspace() for ch in mount):
        raise ConfigError(
            f"Server '{server_name}': reverseProxy.mount must not contain whitespace"
        )
    if ".." in mount:
        raise ConfigError(
            f"Server '{server_name}': reverseProxy.mount must not contain '..'"
        )
    if mount != "/" and mount.endswith("/"):
        raise ConfigError(
            f"Server '{server_name}': reverseProxy.mount must not end with '/' (got {mount!r})"
        )
    if mount in RESERVED_MOUNTS:
        raise ConfigError(
            f"Server '{server_name}': reverseProxy.mount '{mount}' is reserved "
            "(collides with a built-in route)"
        )
    return mount


def _interpolate_env(value: str, server_name: str, field_name: str) -> str:
    """Replace ``${VAR}`` substrings with values from ``os.environ``.

    Raises :class:`ConfigError` when a referenced variable is not set, so
    misconfigured deployments fail loudly at parse time instead of silently
    forwarding ``Authorization: Bearer ${PINCHER_HTTP_KEY}`` literally.
    """
    def _replace(match: re.Match[str]) -> str:
        var = match.group(1)
        env_val = os.environ.get(var)
        if env_val is None:
            raise ConfigError(
                f"Server '{server_name}': {field_name} references "
                f"${{{var}}} but the environment variable is not set"
            )
        return env_val

    return _ENV_VAR_RE.sub(_replace, value)


def _parse_reverse_proxy(server_name: str, raw: Any) -> ReverseProxySpec:
    """Validate and normalize one ``reverseProxy`` block."""
    if not isinstance(raw, dict):
        raise ConfigError(
            f"Server '{server_name}': 'reverseProxy' must be an object"
        )

    mount = _validate_mount(server_name, raw.get("mount"))

    upstream = raw.get("upstream")
    if not isinstance(upstream, str) or not upstream:
        raise ConfigError(
            f"Server '{server_name}': reverseProxy.upstream must be a non-empty string"
        )
    parsed = urlparse(upstream)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigError(
            f"Server '{server_name}': reverseProxy.upstream must be an "
            f"http:// or https:// URL with a host (got {upstream!r})"
        )

    strip_prefix_raw = raw.get("stripPrefix", False)
    if not isinstance(strip_prefix_raw, bool):
        raise ConfigError(
            f"Server '{server_name}': reverseProxy.stripPrefix must be a boolean"
        )

    headers: dict[str, str] = {}
    if "headers" in raw and raw["headers"] is not None:
        headers = _coerce_str_dict(raw["headers"], "reverseProxy.headers", server_name)

    auth_bearer: str | None = None
    auth_raw = raw.get("auth")
    if auth_raw is not None:
        if not isinstance(auth_raw, dict):
            raise ConfigError(
                f"Server '{server_name}': reverseProxy.auth must be an object"
            )
        bearer_raw = auth_raw.get("bearer")
        if bearer_raw is not None:
            if not isinstance(bearer_raw, str):
                raise ConfigError(
                    f"Server '{server_name}': reverseProxy.auth.bearer must be a string"
                )
            auth_bearer = _interpolate_env(
                bearer_raw, server_name, "reverseProxy.auth.bearer"
            )

    return ReverseProxySpec(
        mount=mount.rstrip("/") if mount != "/" else mount,
        upstream=upstream.rstrip("/"),
        strip_prefix=strip_prefix_raw,
        headers=headers,
        auth_bearer=auth_bearer,
    )


def _parse_compress(server_name: str, raw: Any) -> CompressSpec:
    """Validate and normalize one ``compress`` block.

    Both ``level`` and ``scope`` default to the dataclass defaults
    (``medium`` / ``aggregator``) when the keys are absent. An empty
    object ``{}`` therefore expands to a fully-default-configured
    :class:`CompressSpec` rather than being rejected.
    """
    if not isinstance(raw, dict):
        raise ConfigError(
            f"Server '{server_name}': 'compress' must be an object"
        )

    level = raw.get("level", "medium")
    if not isinstance(level, str) or level not in COMPRESS_LEVELS:
        raise ConfigError(
            f"Server '{server_name}': compress.level must be one of "
            f"{sorted(COMPRESS_LEVELS)} (got {level!r})"
        )

    scope = raw.get("scope", "aggregator")
    if not isinstance(scope, str) or scope not in COMPRESS_SCOPES:
        raise ConfigError(
            f"Server '{server_name}': compress.scope must be one of "
            f"{sorted(COMPRESS_SCOPES)} (got {scope!r})"
        )

    return CompressSpec(level=level, scope=scope)


def _parse_server(name: str, raw: Any) -> ServerSpec:
    if not isinstance(raw, dict):
        raise ConfigError(f"Server '{name}': entry must be an object")

    declared_type = raw.get("type")

    reverse_proxy: ReverseProxySpec | None = None
    if "reverseProxy" in raw and raw["reverseProxy"] is not None:
        reverse_proxy = _parse_reverse_proxy(name, raw["reverseProxy"])

    compress: CompressSpec | None = None
    if "compress" in raw and raw["compress"] is not None:
        compress = _parse_compress(name, raw["compress"])

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
            reverse_proxy=reverse_proxy,
            compress=compress,
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
            reverse_proxy=reverse_proxy,
            compress=compress,
        )

    raise ConfigError(
        f"Server '{name}': could not determine transport. "
        "Provide 'command' (stdio) or 'type' set to 'sse' or 'streamable-http'."
    )


def _check_mount_overlap(specs: list[ServerSpec]) -> None:
    """Reject configs where two backends' mounts overlap.

    A mount ``a`` overlaps ``b`` if either is a prefix of the other (treating
    them as path segments, so ``/foo`` overlaps ``/foo/bar`` but not
    ``/foobar``). The first conflicting pair raises :class:`ConfigError`.
    """
    mounts: list[tuple[str, str]] = [
        (s.name, s.reverse_proxy.mount) for s in specs if s.reverse_proxy is not None
    ]
    for i, (name_a, mount_a) in enumerate(mounts):
        for name_b, mount_b in mounts[i + 1 :]:
            if mount_a == mount_b:
                raise ConfigError(
                    f"reverseProxy mount '{mount_a}' is claimed by both "
                    f"'{name_a}' and '{name_b}'"
                )
            # Segment-aware prefix check: '/foo' overlaps '/foo/bar' but
            # NOT '/foobar'. Append '/' so 'startswith' tests segment
            # boundaries correctly.
            longer, shorter = (
                (mount_a, mount_b) if len(mount_a) > len(mount_b) else (mount_b, mount_a)
            )
            if longer.startswith(shorter + "/"):
                raise ConfigError(
                    f"reverseProxy mounts '{mount_a}' ('{name_a}') and "
                    f"'{mount_b}' ('{name_b}') overlap"
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

    _check_mount_overlap(specs)

    primary = raw.get("primaryMCP")
    if primary is not None and not isinstance(primary, str):
        raise ConfigError("'primaryMCP' must be a string")
    # NOTE: primaryMCP is deprecated as of v0.3 — `/mcp` always aggregates every
    # running server. We still parse the field so old pasted configs keep working;
    # ProxyManager.start_all logs a deprecation warning when it sees a value.

    return specs, primary
