from __future__ import annotations

import pytest

from zelosmcp.framework.assetstore.kinds import prompt as prompt_kind
from zelosmcp.framework.assetstore.registry import RepoCtx


def test_prompt_parse_render_and_substitute():
    rows = prompt_kind.PROMPT_KIND.parse_section(
        {
            "find-callers": {
                "description": "Find callers.",
                "args": [
                    {
                        "name": "symbol_name",
                        "description": "Symbol",
                        "required": True,
                    }
                ],
                "targets": ["cursor"],
                "body": "Trace {{ symbol_name }} in both compression modes.",
            }
        },
        "pincher",
        5,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.kind == "prompt"
    assert row.backend == "pincher"
    assert row.name == "find-callers"
    assert row.meta["args"][0]["required"] is True

    rendered = prompt_kind.render_prompt(row, {"symbol_name": "parse_config"})
    assert rendered == "Trace parse_config in both compression modes."

    files = prompt_kind.PROMPT_KIND.render_for_project(
        row,
        RepoCtx(
            name="zelosmcp",
            ro_path="/user_data_ro/zelosmcp",
            rw_path="/user_data_rw/zelosmcp",
        ),
    )
    assert len(files) == 1
    assert files[0].rel_path == ".cursor/commands/find-callers.md"
    assert "description: Find callers." in files[0].body
    assert "Trace {{ symbol_name }}" in files[0].body


def test_prompt_dump_section_round_trips_core_fields():
    rows = prompt_kind.PROMPT_KIND.parse_section(
        {
            "tree-here": {
                "description": "Tree.",
                "args": [{"name": "project", "required": True}],
                "body": "Tree {{project}}",
            }
        },
        "filesystem",
        5,
    )

    dumped = prompt_kind.dump_section(rows)
    assert dumped["tree-here"]["description"] == "Tree."
    assert dumped["tree-here"]["args"][0]["name"] == "project"
    assert dumped["tree-here"]["body"] == "Tree {{project}}"


def test_prompt_render_is_compression_state_independent():
    rows = prompt_kind.PROMPT_KIND.parse_section(
        {
            "find-callers": {
                "description": "Find callers.",
                "args": [{"name": "symbol_name", "required": True}],
                "body": (
                    "Compressed: pincher__invoke_tool {{symbol_name}}\n"
                    "Direct: pincher__trace {{symbol_name}}"
                ),
            }
        },
        "pincher",
        5,
    )
    row = rows[0]

    # The prompt kind deliberately has no compression-state input; both
    # forms live in the body and argument substitution is deterministic.
    a = prompt_kind.render_prompt(row, {"symbol_name": "parse_config"})
    b = prompt_kind.render_prompt(row, {"symbol_name": "parse_config"})
    assert a == b
    assert "pincher__invoke_tool" in a
    assert "pincher__trace" in a


@pytest.mark.asyncio
async def test_builtin_prompts_list_and_get_from_asset_store():
    from zelosmcp.framework.assetstore.row import AssetRow
    from zelosmcp.framework.assetstore.sqlite import SQLiteAssetStore
    from zelosmcp.manager import ProxyManager

    store = SQLiteAssetStore(":memory:")
    await store.open()
    try:
        await store.upsert(AssetRow(
            kind="prompt",
            backend="pincher",
            name="find-callers",
            body=(
                "Compressed: `pincher__invoke_tool(tool_name=\"trace\", "
                "tool_input={\"name\": \"{{symbol_name}}\"})`\n"
                "Direct: `pincher__trace(name=\"{{symbol_name}}\")`"
            ),
            meta={
                "description": "Find callers.",
                "args": [
                    {
                        "name": "symbol_name",
                        "description": "Symbol",
                        "required": True,
                    }
                ],
            },
        ))

        manager = ProxyManager(mandatory_config_path="")
        manager.assets = store
        await manager.builtin.start()
        try:
            listed = await manager.builtin.client_session.list_prompts()
            names = [p.name for p in listed.prompts]
            assert "pincher__find-callers" in names

            result = await manager.builtin.client_session.get_prompt(
                "pincher__find-callers",
                {"symbol_name": "parse_config"},
            )
            text = result.messages[0].content.text
            assert "pincher__invoke_tool" in text
            assert "pincher__trace" in text
            assert "parse_config" in text
            assert "{{symbol_name}}" not in text
        finally:
            await manager.builtin.stop()
    finally:
        await store.close()
