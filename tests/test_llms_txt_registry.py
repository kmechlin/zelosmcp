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


class TestRegistryInvariants:
    """Guard against data quality regressions."""

    def test_no_http_urls(self):
        for name, url in KNOWN_LLMS_TXT.items():
            assert not url.startswith("http://"), (
                f"{name}: plain HTTP not allowed, use HTTPS"
            )

    def test_no_trailing_whitespace_in_keys(self):
        for key in KNOWN_LLMS_TXT:
            assert key == key.strip(), f"Key {key!r} has trailing whitespace"

    def test_no_trailing_whitespace_in_urls(self):
        for name, url in KNOWN_LLMS_TXT.items():
            assert url == url.strip(), (
                f"{name}: URL {url!r} has trailing whitespace"
            )

    def test_no_empty_keys(self):
        for key in KNOWN_LLMS_TXT:
            assert key, "Empty string key found"

    def test_keys_contain_no_slashes(self):
        for key in KNOWN_LLMS_TXT:
            assert "/" not in key, (
                f"Key {key!r} contains a slash — use package name, not path"
            )

    def test_unknown_package_not_in_registry(self):
        assert "this-package-definitely-does-not-exist-xyz" not in KNOWN_LLMS_TXT

    def test_registry_is_not_mutable_accident(self):
        original_len = len(KNOWN_LLMS_TXT)
        KNOWN_LLMS_TXT["_test_sentinel"] = "https://example.com/llms.txt"
        assert len(KNOWN_LLMS_TXT) == original_len + 1
        del KNOWN_LLMS_TXT["_test_sentinel"]
        assert len(KNOWN_LLMS_TXT) == original_len
