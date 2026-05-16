"""Tests for the curated llms.txt URL registry."""
from __future__ import annotations

from zelosmcp.llms_txt_registry import KNOWN_LLMS_TXT


class TestKnownLlmsTxt:
    def test_registry_is_non_empty_dict(self):
        assert isinstance(KNOWN_LLMS_TXT, dict)
        assert len(KNOWN_LLMS_TXT) >= 4

    def test_keys_are_lowercase(self):
        for key in KNOWN_LLMS_TXT:
            assert key == key.lower(), f"Key {key!r} should be lowercase"

    def test_values_are_https_urls(self):
        for name, url in KNOWN_LLMS_TXT.items():
            assert url.startswith("https://"), (
                f"{name}: URL {url!r} must start with https://"
            )

    def test_values_end_with_llms_txt(self):
        for name, url in KNOWN_LLMS_TXT.items():
            assert url.endswith("/llms.txt"), (
                f"{name}: URL {url!r} must end with /llms.txt"
            )

    def test_starter_entries_present(self):
        assert "langgraph" in KNOWN_LLMS_TXT
        assert "langchain" in KNOWN_LLMS_TXT
        assert "fastapi" in KNOWN_LLMS_TXT
        assert "react" in KNOWN_LLMS_TXT

    def test_no_duplicate_urls(self):
        urls = list(KNOWN_LLMS_TXT.values())
        assert len(urls) == len(set(urls)), "Duplicate URLs found"
