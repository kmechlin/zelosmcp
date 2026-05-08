"""Unit tests for zelosmcp.config.parse_config / ServerSpec."""
from __future__ import annotations

import pytest

from zelosmcp.config import (
    COMPRESS_LEVELS,
    COMPRESS_SCOPES,
    CompressSpec,
    ConfigError,
    ReverseProxySpec,
    ServerSpec,
    parse_config,
)


class TestParseStructure:
    def test_non_object_raises(self):
        with pytest.raises(ConfigError, match="JSON object"):
            parse_config([])

    def test_missing_mcpServers(self):
        with pytest.raises(ConfigError, match="mcpServers"):
            parse_config({})

    def test_empty_mcpServers(self):
        with pytest.raises(ConfigError, match="at least one"):
            parse_config({"mcpServers": {}})

    def test_mcpServers_not_object(self):
        with pytest.raises(ConfigError, match="object mapping"):
            parse_config({"mcpServers": []})


class TestStdio:
    def test_basic_command(self):
        specs, primary = parse_config({
            "mcpServers": {
                "fs": {"command": "npx", "args": ["-y", "@m/fs"]},
            }
        })
        assert primary is None
        assert len(specs) == 1
        s = specs[0]
        assert s.name == "fs"
        assert s.transport == "stdio"
        assert s.command == "npx"
        assert s.args == ["-y", "@m/fs"]
        assert s.env is None
        assert s.cwd is None

    def test_command_with_env_and_cwd(self):
        specs, _ = parse_config({
            "mcpServers": {
                "fs": {
                    "command": "uvx",
                    "args": ["server"],
                    "env": {"K": "v"},
                    "cwd": "/tmp",
                }
            }
        })
        s = specs[0]
        assert s.env == {"K": "v"}
        assert s.cwd == "/tmp"

    def test_command_must_be_nonempty(self):
        with pytest.raises(ConfigError, match="non-empty string"):
            parse_config({"mcpServers": {"x": {"command": "  "}}})

    def test_args_must_be_strings(self):
        with pytest.raises(ConfigError, match="array of strings"):
            parse_config({"mcpServers": {"x": {"command": "echo", "args": [1, 2]}}})

    def test_env_must_be_string_map(self):
        with pytest.raises(ConfigError, match="string→string"):
            parse_config({"mcpServers": {"x": {"command": "echo", "env": {"K": 5}}}})

    def test_cwd_type_check(self):
        with pytest.raises(ConfigError, match="cwd"):
            parse_config({"mcpServers": {"x": {"command": "echo", "cwd": 5}}})


class TestRemote:
    def test_sse(self):
        specs, _ = parse_config({
            "mcpServers": {
                "linear": {"type": "sse", "url": "https://x/sse",
                           "headers": {"Authorization": "Bearer t"}}
            }
        })
        s = specs[0]
        assert s.transport == "sse"
        assert s.url == "https://x/sse"
        assert s.headers == {"Authorization": "Bearer t"}

    def test_streamable_http(self):
        specs, _ = parse_config({
            "mcpServers": {
                "gh": {"type": "streamable-http", "url": "https://x/mcp"}
            }
        })
        s = specs[0]
        assert s.transport == "http"
        assert s.url == "https://x/mcp"
        assert s.headers is None

    def test_remote_requires_url(self):
        with pytest.raises(ConfigError, match="requires a 'url'"):
            parse_config({"mcpServers": {"x": {"type": "sse"}}})

    def test_unknown_type(self):
        with pytest.raises(ConfigError, match="determine transport"):
            parse_config({"mcpServers": {"x": {"type": "ws", "url": "ws://"}}})


class TestNames:
    @pytest.mark.parametrize(
        "name",
        ["api", "mcp", "docs", "redoc", "openapi.json", "zelosmcp"],
    )
    def test_reserved_names_rejected(self, name):
        with pytest.raises(ConfigError, match="reserved"):
            parse_config({"mcpServers": {name: {"command": "echo"}}})

    @pytest.mark.parametrize("name", ["bad name", "with/slash", "", "$weird"])
    def test_invalid_names_rejected(self, name):
        with pytest.raises(ConfigError):
            parse_config({"mcpServers": {name: {"command": "echo"}}})

    def test_duplicate_case_insensitive(self):
        with pytest.raises(ConfigError, match="Duplicate"):
            parse_config({"mcpServers": {
                "Foo": {"command": "echo"},
                "foo": {"command": "echo"},
            }})


class TestPrimary:
    def test_primary_resolved(self):
        specs, primary = parse_config({
            "primaryMCP": "b",
            "mcpServers": {
                "a": {"command": "echo"},
                "b": {"command": "echo"},
            },
        })
        assert primary == "b"
        assert [s.name for s in specs] == ["a", "b"]

    def test_unknown_primary_accepted(self):
        # primaryMCP is deprecated — unknown values no longer raise (the field
        # is informational only; ProxyManager.start_all logs a deprecation
        # warning when it sees a value).
        specs, primary = parse_config({
            "primaryMCP": "ghost",
            "mcpServers": {"a": {"command": "echo"}},
        })
        assert [s.name for s in specs] == ["a"]
        assert primary == "ghost"

    def test_primary_must_be_string(self):
        with pytest.raises(ConfigError, match="primaryMCP"):
            parse_config({
                "primaryMCP": 1,
                "mcpServers": {"a": {"command": "echo"}},
            })


class TestServerSpecToStatus:
    def test_stdio_status(self):
        s = ServerSpec(
            name="x", transport="stdio", command="echo",
            args=["a"], env={"K": "v"}, cwd="/tmp",
        )
        d = s.to_status()
        assert d["name"] == "x"
        assert d["transport"] == "stdio"
        assert d["command"] == "echo"
        assert d["args"] == ["a"]
        assert d["env"] == {"K": "v"}
        assert d["cwd"] == "/tmp"

    def test_remote_status(self):
        s = ServerSpec(
            name="x", transport="http", url="https://x",
            headers={"Authorization": "Bearer t"},
        )
        d = s.to_status()
        assert d["url"] == "https://x"
        assert d["headers"] == {"Authorization": "Bearer t"}


def _stdio_with_proxy(rp: dict) -> dict:
    """Build a single-server config wrapping ``rp`` as the reverseProxy block."""
    return {
        "mcpServers": {
            "alpha": {"command": "echo", "args": ["a"], "reverseProxy": rp},
        }
    }


class TestReverseProxy:
    def test_minimal_block_parses(self):
        specs, _ = parse_config(_stdio_with_proxy({
            "mount": "/alpha",
            "upstream": "http://127.0.0.1:8080",
        }))
        rp = specs[0].reverse_proxy
        assert rp is not None
        assert rp.mount == "/alpha"
        assert rp.upstream == "http://127.0.0.1:8080"
        assert rp.strip_prefix is False
        assert rp.headers == {}
        assert rp.auth_bearer is None

    def test_full_block_parses(self):
        specs, _ = parse_config(_stdio_with_proxy({
            "mount": "/alpha",
            "upstream": "https://upstream.example.com:9443",
            "stripPrefix": True,
            "headers": {"X-Custom": "yes"},
            "auth": {"bearer": "literal-token"},
        }))
        rp = specs[0].reverse_proxy
        assert rp is not None
        assert rp.strip_prefix is True
        assert rp.headers == {"X-Custom": "yes"}
        assert rp.auth_bearer == "literal-token"

    def test_remote_backend_can_have_proxy(self):
        specs, _ = parse_config({
            "mcpServers": {
                "alpha": {
                    "type": "streamable-http",
                    "url": "http://x/mcp",
                    "reverseProxy": {
                        "mount": "/alpha",
                        "upstream": "http://127.0.0.1:8080",
                    },
                },
            }
        })
        assert specs[0].reverse_proxy is not None

    def test_missing_proxy_field_is_optional(self):
        specs, _ = parse_config({
            "mcpServers": {"alpha": {"command": "echo"}},
        })
        assert specs[0].reverse_proxy is None

    @pytest.mark.parametrize(
        "mount, message",
        [
            ("alpha", "must start with"),       # no leading slash
            ("/alpha/", "must not end with"),   # trailing slash
            ("/foo/../bar", "must not contain '..'"),
            ("/al pha", "whitespace"),
            ("/api", "reserved"),
            ("/mcp", "reserved"),
            ("/", "reserved"),
            ("/docs", "reserved"),
        ],
    )
    def test_bad_mounts_rejected(self, mount, message):
        with pytest.raises(ConfigError, match=message):
            parse_config(_stdio_with_proxy({
                "mount": mount,
                "upstream": "http://127.0.0.1:8080",
            }))

    def test_missing_mount_rejected(self):
        with pytest.raises(ConfigError, match="mount"):
            parse_config(_stdio_with_proxy({"upstream": "http://x:8080"}))

    def test_missing_upstream_rejected(self):
        with pytest.raises(ConfigError, match="upstream"):
            parse_config(_stdio_with_proxy({"mount": "/alpha"}))

    @pytest.mark.parametrize(
        "upstream",
        ["", "ftp://upstream", "not a url", "http://"],
    )
    def test_bad_upstream_rejected(self, upstream):
        with pytest.raises(ConfigError, match="upstream"):
            parse_config(_stdio_with_proxy({
                "mount": "/alpha",
                "upstream": upstream,
            }))

    def test_strip_prefix_must_be_bool(self):
        with pytest.raises(ConfigError, match="stripPrefix"):
            parse_config(_stdio_with_proxy({
                "mount": "/alpha",
                "upstream": "http://x:8080",
                "stripPrefix": "yes",
            }))

    def test_headers_must_be_string_map(self):
        with pytest.raises(ConfigError, match="reverseProxy.headers"):
            parse_config(_stdio_with_proxy({
                "mount": "/alpha",
                "upstream": "http://x:8080",
                "headers": {"X": 1},
            }))

    def test_auth_must_be_object(self):
        with pytest.raises(ConfigError, match="auth must be an object"):
            parse_config(_stdio_with_proxy({
                "mount": "/alpha",
                "upstream": "http://x:8080",
                "auth": "Bearer xyz",
            }))

    def test_auth_bearer_env_interpolation(self, monkeypatch):
        monkeypatch.setenv("PINCHER_HTTP_KEY", "s3cret")
        specs, _ = parse_config(_stdio_with_proxy({
            "mount": "/alpha",
            "upstream": "http://x:8080",
            "auth": {"bearer": "${PINCHER_HTTP_KEY}"},
        }))
        assert specs[0].reverse_proxy.auth_bearer == "s3cret"

    def test_auth_bearer_missing_env_var_raises(self, monkeypatch):
        monkeypatch.delenv("PINCHER_HTTP_KEY", raising=False)
        with pytest.raises(ConfigError, match="not set"):
            parse_config(_stdio_with_proxy({
                "mount": "/alpha",
                "upstream": "http://x:8080",
                "auth": {"bearer": "${PINCHER_HTTP_KEY}"},
            }))

    def test_overlapping_mounts_rejected_exact(self):
        with pytest.raises(ConfigError, match="claimed by both"):
            parse_config({
                "mcpServers": {
                    "a": {
                        "command": "echo",
                        "reverseProxy": {"mount": "/dup", "upstream": "http://a:1"},
                    },
                    "b": {
                        "command": "echo",
                        "reverseProxy": {"mount": "/dup", "upstream": "http://b:1"},
                    },
                }
            })

    def test_overlapping_mounts_rejected_prefix(self):
        with pytest.raises(ConfigError, match="overlap"):
            parse_config({
                "mcpServers": {
                    "a": {
                        "command": "echo",
                        "reverseProxy": {"mount": "/foo", "upstream": "http://a:1"},
                    },
                    "b": {
                        "command": "echo",
                        "reverseProxy": {"mount": "/foo/bar", "upstream": "http://b:1"},
                    },
                }
            })

    def test_sibling_mounts_allowed(self):
        # `/foo` and `/foobar` look prefix-y but split on segment boundaries.
        specs, _ = parse_config({
            "mcpServers": {
                "a": {
                    "command": "echo",
                    "reverseProxy": {"mount": "/foo", "upstream": "http://a:1"},
                },
                "b": {
                    "command": "echo",
                    "reverseProxy": {"mount": "/foobar", "upstream": "http://b:1"},
                },
            }
        })
        assert {s.name for s in specs} == {"a", "b"}

    def test_to_status_round_trip(self):
        rp = ReverseProxySpec(
            mount="/alpha",
            upstream="http://x:8080",
            strip_prefix=True,
            headers={"X-Custom": "v"},
            auth_bearer="s3cret",
        )
        out = rp.to_status()
        assert out["mount"] == "/alpha"
        assert out["upstream"] == "http://x:8080"
        assert out["stripPrefix"] is True
        assert out["headers"] == {"X-Custom": "v"}
        # Bearer is masked so /api/status doesn't leak the secret.
        assert out["auth"] == {"bearer": "***"}

    def test_to_status_omits_optional_fields_when_default(self):
        rp = ReverseProxySpec(mount="/alpha", upstream="http://x:8080")
        out = rp.to_status()
        assert "stripPrefix" not in out
        assert "headers" not in out
        assert "auth" not in out

    def test_server_spec_to_status_includes_proxy(self):
        s = ServerSpec(
            name="alpha",
            transport="stdio",
            command="echo",
            reverse_proxy=ReverseProxySpec(
                mount="/alpha",
                upstream="http://x:8080",
            ),
        )
        d = s.to_status()
        assert d["reverseProxy"] == {
            "mount": "/alpha",
            "upstream": "http://x:8080",
        }


_OMITTED = object()  # sentinel: do not set the key at all


def _stdio_with_compress(rp) -> dict:
    """Build a single-server config wrapping ``rp`` as the compress block.

    Pass the ``_OMITTED`` sentinel to omit the key entirely (exercises
    the default-on path); pass ``None`` / ``False`` / a dict to set it
    explicitly.
    """
    entry: dict = {"command": "echo", "args": ["a"]}
    if rp is not _OMITTED:
        entry["compress"] = rp
    return {"mcpServers": {"alpha": entry}}


class TestCompressSpec:
    def test_block_absent_defaults_to_medium_aggregator(self):
        # Omitting the block is the recommended path: every backend
        # gets `medium` compression at the aggregator unless it opts
        # out explicitly.
        specs, _ = parse_config(_stdio_with_compress(_OMITTED))
        c = specs[0].compress
        assert c is not None
        assert c.level == "medium"
        assert c.scope == "aggregator"

    def test_explicit_null_disables_compress(self):
        # `compress: null` is the documented opt-out form.
        specs, _ = parse_config(_stdio_with_compress(None))
        assert specs[0].compress is None

    def test_explicit_false_disables_compress(self):
        # JSON booleans are accepted as a convenience opt-out form.
        specs, _ = parse_config(_stdio_with_compress(False))
        assert specs[0].compress is None

    def test_empty_block_uses_defaults(self):
        # `compress: {}` is equivalent to omitting the block.
        specs, _ = parse_config(_stdio_with_compress({}))
        c = specs[0].compress
        assert c is not None
        assert c.level == "medium"
        assert c.scope == "aggregator"

    @pytest.mark.parametrize("level", sorted(COMPRESS_LEVELS))
    def test_each_level_accepted(self, level):
        specs, _ = parse_config(_stdio_with_compress({"level": level}))
        assert specs[0].compress.level == level

    @pytest.mark.parametrize("scope", sorted(COMPRESS_SCOPES))
    def test_each_scope_accepted(self, scope):
        specs, _ = parse_config(_stdio_with_compress({"scope": scope}))
        assert specs[0].compress.scope == scope

    def test_full_block_round_trips(self):
        specs, _ = parse_config(
            _stdio_with_compress({"level": "high", "scope": "global"})
        )
        c = specs[0].compress
        assert c.level == "high"
        assert c.scope == "global"
        assert c.to_status() == {"level": "high", "scope": "global"}

    def test_unknown_level_rejected(self):
        with pytest.raises(ConfigError, match="compress.level"):
            parse_config(_stdio_with_compress({"level": "ultra"}))

    def test_unknown_scope_rejected(self):
        with pytest.raises(ConfigError, match="compress.scope"):
            parse_config(_stdio_with_compress({"scope": "everything"}))

    def test_non_object_block_rejected(self):
        with pytest.raises(ConfigError, match="must be an object"):
            parse_config(_stdio_with_compress("medium"))  # type: ignore[arg-type]

    def test_remote_backend_can_have_compress(self):
        specs, _ = parse_config({
            "mcpServers": {
                "alpha": {
                    "type": "streamable-http",
                    "url": "http://x/mcp",
                    "compress": {"level": "max", "scope": "catalog"},
                },
            }
        })
        c = specs[0].compress
        assert c.level == "max"
        assert c.scope == "catalog"

    def test_server_spec_to_status_includes_compress(self):
        s = ServerSpec(
            name="alpha",
            transport="stdio",
            command="echo",
            compress=CompressSpec(level="high", scope="global"),
        )
        d = s.to_status()
        assert d["compress"] == {"level": "high", "scope": "global"}
