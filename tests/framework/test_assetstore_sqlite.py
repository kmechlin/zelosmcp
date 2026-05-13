"""Unit tests for SQLiteAssetStore."""
from __future__ import annotations

import pytest

from zelosmcp.framework.assetstore.row import AssetRow
from zelosmcp.framework.assetstore.sqlite import SQLiteAssetStore


@pytest.fixture
async def store():
    s = SQLiteAssetStore(":memory:")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


@pytest.mark.asyncio
class TestOpenClose:
    async def test_open_idempotent(self, store):
        await store.open()  # second open should not raise
        assert store._db is not None

    async def test_close_idempotent(self, store):
        await store.close()
        await store.close()  # second close should not raise


@pytest.mark.asyncio
class TestGetAndUpsert:
    async def test_upsert_and_get(self, store):
        row = AssetRow(kind="rule", backend="pincher", name="playbook_ro", body="Hello")
        await store.upsert(row)
        fetched = await store.get("rule", "pincher", "playbook_ro")
        assert fetched is not None
        assert fetched.body == "Hello"

    async def test_get_missing_returns_none(self, store):
        assert await store.get("rule", "ghost", "missing") is None

    async def test_upsert_overwrites(self, store):
        row = AssetRow(kind="rule", backend="b", name="n", body="v1")
        await store.upsert(row)
        row2 = AssetRow(kind="rule", backend="b", name="n", body="v2")
        await store.upsert(row2)
        assert (await store.get("rule", "b", "n")).body == "v2"

    async def test_meta_round_trips(self, store):
        row = AssetRow(kind="extension", backend="pincher", name="idx", meta={"tool": "index"})
        await store.upsert(row)
        fetched = await store.get("extension", "pincher", "idx")
        assert fetched.meta == {"tool": "index"}

    async def test_target_is_part_of_pk(self, store):
        r1 = AssetRow(kind="rule", backend="b", name="n", target="cursor", body="c")
        r2 = AssetRow(kind="rule", backend="b", name="n", target="vscode", body="v")
        await store.upsert(r1)
        await store.upsert(r2)
        assert (await store.get("rule", "b", "n", "cursor")).body == "c"
        assert (await store.get("rule", "b", "n", "vscode")).body == "v"


@pytest.mark.asyncio
class TestConditionalUpsert:
    async def test_seed_row_not_overwritten_by_lower_version(self, store):
        row_v2 = AssetRow(kind="rule", backend="b", name="n", body="v2", source="seed", seed_version=2)
        await store.upsert(row_v2)
        row_v1 = AssetRow(kind="rule", backend="b", name="n", body="v1", source="seed", seed_version=1)
        written = await store.upsert(row_v1, only_if_seed_lt=1)
        assert written is False
        assert (await store.get("rule", "b", "n")).body == "v2"

    async def test_seed_row_overwritten_by_higher_version(self, store):
        row_v1 = AssetRow(kind="rule", backend="b", name="n", body="v1", source="seed", seed_version=1)
        await store.upsert(row_v1)
        row_v2 = AssetRow(kind="rule", backend="b", name="n", body="v2", source="seed", seed_version=2)
        written = await store.upsert(row_v2, only_if_seed_lt=2)
        assert written is True
        assert (await store.get("rule", "b", "n")).body == "v2"

    async def test_user_row_never_overwritten_by_seed(self, store):
        user_row = AssetRow(kind="rule", backend="b", name="n", body="my edit", source="user")
        await store.upsert(user_row)
        seed_row = AssetRow(kind="rule", backend="b", name="n", body="seed", source="seed", seed_version=99)
        written = await store.upsert(seed_row, only_if_seed_lt=99)
        assert written is False
        assert (await store.get("rule", "b", "n")).body == "my edit"

    async def test_new_row_always_written(self, store):
        row = AssetRow(kind="rule", backend="b", name="new_n", body="x", source="seed", seed_version=1)
        written = await store.upsert(row, only_if_seed_lt=1)
        assert written is True


@pytest.mark.asyncio
class TestList:
    async def test_list_all(self, store):
        await store.upsert(AssetRow(kind="rule", backend="a", name="x"))
        await store.upsert(AssetRow(kind="extension", backend="a", name="y"))
        rows = await store.list()
        assert len(rows) == 2

    async def test_list_filtered_by_kind(self, store):
        await store.upsert(AssetRow(kind="rule", backend="a", name="x"))
        await store.upsert(AssetRow(kind="extension", backend="a", name="y"))
        rules = await store.list(kind="rule")
        assert all(r.kind == "rule" for r in rules)
        assert len(rules) == 1

    async def test_list_filtered_by_backend(self, store):
        await store.upsert(AssetRow(kind="rule", backend="pincher", name="x"))
        await store.upsert(AssetRow(kind="rule", backend="filesystem", name="y"))
        rows = await store.list(backend="pincher")
        assert all(r.backend == "pincher" for r in rows)


@pytest.mark.asyncio
class TestDelete:
    async def test_delete_returns_true_on_hit(self, store):
        await store.upsert(AssetRow(kind="rule", backend="b", name="n"))
        assert await store.delete("rule", "b", "n") is True
        assert await store.get("rule", "b", "n") is None

    async def test_delete_returns_false_on_miss(self, store):
        assert await store.delete("rule", "ghost", "missing") is False


@pytest.mark.asyncio
class TestSummary:
    async def test_summary_counts(self, store):
        await store.upsert(AssetRow(kind="rule", backend="b", name="x", source="seed"))
        await store.upsert(AssetRow(kind="extension", backend="b", name="y", source="user"))
        s = await store.summary()
        assert s["total"] == 2
        assert s["by_kind"]["rule"] == 1
        assert s["by_source"]["seed"] == 1
