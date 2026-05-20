"""Unit tests for the repository discovery scanner and the /api/repos
HTTP surface.

Coverage:
  - ``zelosmcp.repos.discover_repos`` — shallow walk, skip-list, has_rule
    flag, ro/rw prefix swap.
  - ``zelosmcp.repos.is_under_scan_root`` / ``rule_target`` —
    path-safety helpers used by the POST handlers.
  - ``zelosmcp.app._extract_pincher_indexed_paths`` — pincher__list payload
    -> set of indexed repo paths.
  - GET ``/api/repos`` — returns the discovered list, marks pincher_indexed
    when the running pincher backend lists the repo, and gracefully
    degrades when pincher is absent or errors.
  - POST ``/api/repos/write-rule`` — calls render_comprehensive_rule, then
    forwards create_directory + write_file to a stubbed filesystem MCP.
  - POST ``/api/repos/index`` — forwards index calls to a stubbed pincher
    MCP and reflects the structured response.

The scanner is configured via ``ZELOSMCP_REPO_SCAN_ROOT`` /
``ZELOSMCP_REPO_RW_ROOT`` so we can point it at ``tmp_path`` instead of
the real ``/user_data_ro`` mount. Each test patches
``zelosmcp.repos._CACHE`` to start fresh.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from zelosmcp import repos as repos_mod
from zelosmcp.app import create_app
from zelosmcp.openapi import (
    extract_pincher_indexed_paths as _extract_pincher_indexed_paths,
    flatten_call_result as _flatten_call_result,
)
from zelosmcp.manager import ProxyManager


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_repo_cache():
    """Ensure each test starts with a clean discovery cache so prior runs
    can't leak into the next."""
    repos_mod._CACHE.repos = []
    repos_mod._CACHE.expires_at = 0.0
    yield
    repos_mod._CACHE.repos = []
    repos_mod._CACHE.expires_at = 0.0


@pytest.fixture
def repo_tree(tmp_path, monkeypatch):
    """Create a shallow workspace under tmp_path with two real repos, a
    nested-but-skipped ``node_modules`` clone, and a non-repo directory.

    Layout::

        <tmp_path>/repo_a/.git/
        <tmp_path>/repo_a/.cursor/rules/zelosmcp.mdc   (has_rule = True)
        <tmp_path>/repo_b/.git
        <tmp_path>/notes/                              (no .git)
        <tmp_path>/skip/node_modules/inner/.git/       (skipped)
        <tmp_path>/repo_a/vendor/sub/.git/             (nested, not yielded)

    The ``ZELOSMCP_REPO_RW_ROOT`` env var is pointed at a sibling so the
    prefix-swap logic has a real distinct path to map to.
    """
    ro_root = tmp_path / "user_data_ro"
    rw_root = tmp_path / "user_data_rw"
    ro_root.mkdir()
    rw_root.mkdir()

    (ro_root / "repo_a" / ".git").mkdir(parents=True)
    (ro_root / "repo_a" / ".cursor" / "rules").mkdir(parents=True)
    (ro_root / "repo_a" / ".cursor" / "rules" / "zelosmcp.mdc").write_text("# old rule")
    (ro_root / "repo_a" / "vendor" / "sub" / ".git").mkdir(parents=True)

    (ro_root / "repo_b" / ".git").mkdir(parents=True)

    (ro_root / "notes").mkdir()
    (ro_root / "notes" / "README.md").write_text("not a repo")

    (ro_root / "skip" / "node_modules" / "inner" / ".git").mkdir(parents=True)

    monkeypatch.setenv("ZELOSMCP_REPO_SCAN_ROOT", str(ro_root))
    monkeypatch.setenv("ZELOSMCP_REPO_RW_ROOT", str(rw_root))
    monkeypatch.setenv("ZELOSMCP_REPO_SCAN_DEPTH", "4")
    return SimpleNamespace(ro=ro_root, rw=rw_root)


# ── Scanner ─────────────────────────────────────────────────────────────


class TestDiscoverRepos:
    def test_finds_top_level_repos(self, repo_tree):
        out = repos_mod.discover_repos(refresh=True)
        names = sorted(r.name for r in out)
        assert names == ["repo_a", "repo_b"]

    def test_swaps_ro_prefix_for_rw(self, repo_tree):
        out = {r.name: r for r in repos_mod.discover_repos(refresh=True)}
        a = out["repo_a"]
        assert a.path_ro == str(repo_tree.ro / "repo_a")
        assert a.path_rw == str(repo_tree.rw / "repo_a")
        assert a.path_rw.startswith(str(repo_tree.rw))

    def test_has_rule_flag_reflects_existing_mdc(self, repo_tree):
        out = {r.name: r for r in repos_mod.discover_repos(refresh=True)}
        assert out["repo_a"].has_rule is True
        assert out["repo_b"].has_rule is False

    def test_skip_list_prunes_node_modules(self, repo_tree):
        out = repos_mod.discover_repos(refresh=True)
        for r in out:
            assert "node_modules" not in r.path_ro

    def test_does_not_descend_into_discovered_repo(self, repo_tree):
        # repo_a/vendor/sub is a real .git dir, but the walker should
        # have stopped descending after yielding repo_a — so it must
        # not appear as its own row.
        out = repos_mod.discover_repos(refresh=True)
        paths = {r.path_ro for r in out}
        assert str(repo_tree.ro / "repo_a" / "vendor" / "sub") not in paths

    def test_cache_returns_same_objects_within_ttl(self, repo_tree):
        first = repos_mod.discover_repos()
        second = repos_mod.discover_repos()
        assert [r.path_ro for r in first] == [r.path_ro for r in second]

    def test_refresh_busts_cache(self, repo_tree):
        first = repos_mod.discover_repos()
        # Add a new repo and make sure refresh=True picks it up.
        (repo_tree.ro / "repo_c" / ".git").mkdir(parents=True)
        cached = repos_mod.discover_repos()
        assert {r.name for r in cached} == {r.name for r in first}
        refreshed = repos_mod.discover_repos(refresh=True)
        assert "repo_c" in {r.name for r in refreshed}

    def test_missing_root_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "ZELOSMCP_REPO_SCAN_ROOT", str(tmp_path / "does-not-exist")
        )
        monkeypatch.setenv("ZELOSMCP_REPO_RW_ROOT", str(tmp_path / "rw"))
        out = repos_mod.discover_repos(refresh=True)
        assert out == []

    def test_root_is_itself_a_repo_does_not_shadow_children(self, repo_tree):
        """Regression: when ``$HOME`` is version-controlled for dotfiles
        the scan root contains its own ``.git`` directory. The previous
        walker yielded the root as the single match and never descended,
        hiding every actual project below. The new walker treats the root
        as a normal directory and still surfaces nested repos."""
        # Add a .git at the scan-root level (simulating ~/.git for dotfiles).
        (repo_tree.ro / ".git").mkdir()
        out = repos_mod.discover_repos(refresh=True)
        names = sorted(r.name for r in out)
        # Root itself ("user_data_ro") MUST NOT be yielded, but the two
        # real child repos must still be there.
        assert names == ["repo_a", "repo_b"]
        for r in out:
            assert r.path_ro != str(repo_tree.ro)

    def test_finds_repos_at_depth_3(self, tmp_path, monkeypatch):
        """The default scan depth should be deep enough for layouts like
        ``~/workspace/<group>/<repo>`` (depth 2) and one more level of
        nesting for client-organized trees."""
        ro_root = tmp_path / "ro"
        rw_root = tmp_path / "rw"
        deep = ro_root / "workspace" / "clients" / "acme" / "deep_repo"
        (deep / ".git").mkdir(parents=True)
        rw_root.mkdir()
        monkeypatch.setenv("ZELOSMCP_REPO_SCAN_ROOT", str(ro_root))
        monkeypatch.setenv("ZELOSMCP_REPO_RW_ROOT", str(rw_root))
        out = repos_mod.discover_repos(refresh=True)
        names = [r.name for r in out]
        assert "deep_repo" in names


# ── Path-safety helpers ────────────────────────────────────────────────


class TestPathHelpers:
    def test_is_under_scan_root_accepts_repo(self, repo_tree):
        assert repos_mod.is_under_scan_root(str(repo_tree.ro / "repo_a"))

    def test_is_under_scan_root_rejects_outside(self, repo_tree, tmp_path):
        assert not repos_mod.is_under_scan_root(str(tmp_path))
        assert not repos_mod.is_under_scan_root("/etc/passwd")
        assert not repos_mod.is_under_scan_root("")
        assert not repos_mod.is_under_scan_root(None)  # type: ignore[arg-type]

    def test_is_under_scan_root_rejects_lookalike_prefix(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("ZELOSMCP_REPO_SCAN_ROOT", str(tmp_path / "ro"))
        # /tmp/ro_evil/... must NOT match /tmp/ro
        assert not repos_mod.is_under_scan_root(
            str(tmp_path / "ro_evil" / "x")
        )

    def test_rule_target_cursor_mdc(self, repo_tree):
        repo_ro = str(repo_tree.ro / "repo_a")
        target = repos_mod.rule_target(repo_ro, "cursor-mdc")
        assert target == str(
            repo_tree.rw / "repo_a" / ".cursor" / "rules" / "zelosmcp.mdc"
        )

    def test_rule_target_copilot(self, repo_tree):
        repo_ro = str(repo_tree.ro / "repo_a")
        target = repos_mod.rule_target(repo_ro, "copilot-instructions")
        assert target == str(
            repo_tree.rw / "repo_a" / ".github" / "copilot-instructions.md"
        )

    def test_rule_target_unknown_format_raises(self, repo_tree):
        with pytest.raises(ValueError):
            repos_mod.rule_target(str(repo_tree.ro / "repo_a"), "bogus")


# ── _flatten_call_result / _extract_pincher_indexed_paths ──────────────


class _FakeText:
    def __init__(self, text):
        self.text = text


class _FakeResult:
    def __init__(self, *, content=None, structuredContent=None, isError=False):
        self.content = content or []
        self.structuredContent = structuredContent
        self.isError = isError


class TestPincherListExtraction:
    def test_extract_from_text_content(self):
        result = _FakeResult(
            content=[_FakeText('{"projects":[{"name":"a","path":"/p/a"}]}')]
        )
        assert _extract_pincher_indexed_paths(result) == {"/p/a"}

    def test_extract_from_structured_content(self):
        result = _FakeResult(
            structuredContent={
                "projects": [
                    {"name": "a", "path": "/p/a"},
                    {"name": "b", "path": "/p/b"},
                ]
            }
        )
        assert _extract_pincher_indexed_paths(result) == {"/p/a", "/p/b"}

    def test_extract_handles_capitalized_path_key(self):
        # The pincher response we observed in CallMcpTool used both lower-
        # and upper-case "Path" keys; cover that.
        result = _FakeResult(
            structuredContent={"projects": [{"Path": "/p/a"}]}
        )
        assert _extract_pincher_indexed_paths(result) == {"/p/a"}

    def test_extract_returns_empty_set_on_garbage(self):
        assert _extract_pincher_indexed_paths(_FakeResult(content=[_FakeText("not json")])) == set()
        assert _extract_pincher_indexed_paths(_FakeResult()) == set()

    def test_flatten_falls_back_to_text(self):
        result = _FakeResult(content=[_FakeText("plain message")])
        assert _flatten_call_result(result) == "plain message"


# ── HTTP API ───────────────────────────────────────────────────────────


def _make_app(repo_tree):
    """Build a ProxyManager+app pair with mandatory-merge disabled (so
    nothing reaches out to /app/configs/mandatory-zelosmcp.json from inside
    the test sandbox) and stub out the filesystem and pincher backends."""
    manager = ProxyManager(mandatory_config_path="")
    app = create_app(manager)
    return app, manager


def _stub_running_backend(name, manager, *, call_tool_side_effect=None):
    """Drop a fake running backend into ``manager.servers`` with an
    AsyncMock-backed client_session.call_tool. Returns the AsyncMock so
    the caller can assert on it."""
    call_tool = AsyncMock()
    if call_tool_side_effect is not None:
        call_tool.side_effect = call_tool_side_effect
    else:
        call_tool.return_value = _FakeResult(
            content=[_FakeText("ok")], isError=False
        )
    session = MagicMock()
    session.call_tool = call_tool
    state = SimpleNamespace(
        name=name,
        running=True,
        client_session=session,
        backend_info={"transport": "stdio"},
        error=None,
    )
    manager.servers[name] = state
    return call_tool


def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


class TestApiReposList:
    @pytest.mark.asyncio
    async def test_returns_discovered_repos_without_pincher(self, repo_tree):
        app, _ = _make_app(repo_tree)
        async with _client(app) as c:
            r = await c.get("/api/repos")
        assert r.status_code == 200
        data = r.json()
        names = sorted(x["name"] for x in data["repos"])
        assert names == ["repo_a", "repo_b"]
        # Pincher absent => pincher_indexed flag is uniformly False.
        assert all(x["pincher_indexed"] is False for x in data["repos"])

    @pytest.mark.asyncio
    async def test_marks_pincher_indexed_when_listed(self, repo_tree):
        app, manager = _make_app(repo_tree)
        repo_a_path = str(repo_tree.ro / "repo_a")
        list_result = _FakeResult(
            structuredContent={"projects": [{"name": "repo_a", "path": repo_a_path}]}
        )
        _stub_running_backend(
            "pincher", manager,
            call_tool_side_effect=lambda *a, **kw: list_result,
        )
        async with _client(app) as c:
            r = await c.get("/api/repos")
        assert r.status_code == 200
        data = r.json()
        a = next(x for x in data["repos"] if x["name"] == "repo_a")
        b = next(x for x in data["repos"] if x["name"] == "repo_b")
        assert a["pincher_indexed"] is True
        assert b["pincher_indexed"] is False

    @pytest.mark.asyncio
    async def test_pincher_failure_does_not_500(self, repo_tree):
        app, manager = _make_app(repo_tree)

        async def boom(*_a, **_kw):
            raise RuntimeError("pincher exploded")

        _stub_running_backend("pincher", manager, call_tool_side_effect=boom)
        async with _client(app) as c:
            r = await c.get("/api/repos")
        assert r.status_code == 200
        data = r.json()
        assert all(x["pincher_indexed"] is False for x in data["repos"])

    @pytest.mark.asyncio
    async def test_refresh_query_busts_cache(self, repo_tree):
        app, _ = _make_app(repo_tree)
        async with _client(app) as c:
            await c.get("/api/repos")
            (repo_tree.ro / "repo_c" / ".git").mkdir(parents=True)
            stale = await c.get("/api/repos")
            fresh = await c.get("/api/repos?refresh=1")
        stale_names = {x["name"] for x in stale.json()["repos"]}
        fresh_names = {x["name"] for x in fresh.json()["repos"]}
        assert "repo_c" not in stale_names
        assert "repo_c" in fresh_names


class TestApiRepoWriteRule:
    @pytest.mark.asyncio
    async def test_writes_rule_to_disk(self, repo_tree):
        app, manager = _make_app(repo_tree)
        repo_a = str(repo_tree.ro / "repo_a")
        body = {"path": repo_a, "format": "cursor-mdc"}
        async with _client(app) as c:
            r = await c.post("/api/repos/write-rule", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        expected_target = str(
            repo_tree.rw / "repo_a" / ".cursor" / "rules" / "zelosmcp.mdc"
        )
        assert data["path"] == expected_target
        assert data["bytes"] > 0
        # Verify the file was actually written to disk.
        import pathlib
        written = pathlib.Path(expected_target)
        assert written.exists()
        assert "zelosMCP" in written.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_copilot_format_writes_to_github_dir(self, repo_tree):
        app, manager = _make_app(repo_tree)
        repo_a = str(repo_tree.ro / "repo_a")
        async with _client(app) as c:
            r = await c.post(
                "/api/repos/write-rule",
                json={"path": repo_a, "format": "copilot-instructions"},
            )
        assert r.status_code == 200, r.text
        expected_target = str(
            repo_tree.rw / "repo_a" / ".github" / "copilot-instructions.md"
        )
        import pathlib
        written = pathlib.Path(expected_target)
        assert written.exists()

    @pytest.mark.asyncio
    async def test_path_outside_scan_root_rejected(self, repo_tree, tmp_path):
        app, manager = _make_app(repo_tree)
        async with _client(app) as c:
            r = await c.post(
                "/api/repos/write-rule",
                json={"path": str(tmp_path / "evil")},
            )
        assert r.status_code == 400
        assert "must be under" in r.json()["error"]

    @pytest.mark.asyncio
    async def test_unknown_access_rejected(self, repo_tree):
        app, manager = _make_app(repo_tree)
        repo_a = str(repo_tree.ro / "repo_a")
        async with _client(app) as c:
            r = await c.post(
                "/api/repos/write-rule",
                json={"path": repo_a, "access": "bogus"},
            )
        assert r.status_code == 400
        assert "access" in r.json()["error"].lower()
