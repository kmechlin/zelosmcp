"""Unit tests for localmcp.config.parse_config / ServerSpec."""
from __future__ import annotations

import pytest

from localmcp.config import ConfigError, ServerSpec, parse_config


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
    @pytest.mark.parametrize("name", ["api", "mcp", "docs", "redoc", "openapi.json"])
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

    def test_unknown_primary(self):
        with pytest.raises(ConfigError, match="unknown server"):
            parse_config({
                "primaryMCP": "ghost",
                "mcpServers": {"a": {"command": "echo"}},
            })

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
