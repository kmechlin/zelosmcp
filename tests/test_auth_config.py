"""Unit tests for PR 2 of the auth-provider framework.

Covers:

- :func:`zelosmcp.config.parse_auth_providers` — top-level
  ``providers`` mapping in ``configs/auth-providers.json``.
- Per-backend ``auth: { provider, audience }`` field on the
  existing :func:`parse_config` path (``ServerSpec.auth_provider``,
  ``ServerSpec.auth_audience``).
- :func:`zelosmcp.config.validate_provider_references` cross-check
  between server specs and provider specs.
- ``ServerSpec.to_status`` redaction for the new auth fields.
- :class:`zelosmcp.auth.factory.build_provider` for legacy types.

End-to-end manager wiring + HTTP routes are exercised in
``tests/test_app_integration.py`` patches.
"""
from __future__ import annotations

import pytest

from zelosmcp.auth import (
    PassthroughProvider,
    ProviderTypeUnavailable,
    StaticBearerProvider,
    build_provider,
)
from zelosmcp.config import (
    AUTH_PROVIDER_TYPES,
    AuthProviderSpec,
    ConfigError,
    parse_auth_providers,
    parse_config,
    validate_provider_references,
)


# ── parse_auth_providers ────────────────────────────────────────────────


class TestParseAuthProvidersStructure:
    def test_non_object_raises(self):
        with pytest.raises(ConfigError, match="JSON object"):
            parse_auth_providers([])

    def test_missing_providers_key_yields_empty(self):
        # Tolerant of the empty-config case so a fresh deployment with
        # no providers configured boots cleanly.
        assert parse_auth_providers({}) == {}

    def test_explicit_empty_providers_yields_empty(self):
        assert parse_auth_providers({"providers": {}}) == {}

    def test_providers_must_be_object(self):
        with pytest.raises(ConfigError, match="object mapping"):
            parse_auth_providers({"providers": []})

    def test_known_provider_types(self):
        # Sanity: the constant matches what the parser accepts.
        assert AUTH_PROVIDER_TYPES == frozenset({
            "github_device_flow",
            "okta_device_flow",
            "passthrough",
            "static",
        })


class TestParseAuthProvidersGithub:
    def test_minimal_github_provider(self):
        out = parse_auth_providers({
            "providers": {
                "gh": {
                    "type": "github_device_flow",
                    "client_id": "Iv1.public_id",
                }
            }
        })
        assert "gh" in out
        spec = out["gh"]
        assert spec.type == "github_device_flow"
        assert spec.client_id == "Iv1.public_id"
        assert spec.scopes == []
        assert spec.bearer is None
        assert spec.issuer is None
        assert spec.membership_hint is None

    def test_github_with_scopes(self):
        out = parse_auth_providers({
            "providers": {
                "gh": {
                    "type": "github_device_flow",
                    "client_id": "Iv1.x",
                    "scopes": ["repo", "read:org"],
                }
            }
        })
        assert out["gh"].scopes == ["repo", "read:org"]

    def test_github_requires_client_id(self):
        with pytest.raises(ConfigError, match="missing required.*client_id"):
            parse_auth_providers({
                "providers": {"gh": {"type": "github_device_flow"}}
            })

    def test_github_rejects_issuer(self):
        # Unrecognised field for type. Catches typos like setting
        # issuer on a github provider.
        with pytest.raises(ConfigError, match="unrecognised"):
            parse_auth_providers({
                "providers": {
                    "gh": {
                        "type": "github_device_flow",
                        "client_id": "Iv1.x",
                        "issuer": "https://example.com",
                    }
                }
            })

    def test_github_rejects_bearer(self):
        with pytest.raises(ConfigError, match="unrecognised"):
            parse_auth_providers({
                "providers": {
                    "gh": {
                        "type": "github_device_flow",
                        "client_id": "Iv1.x",
                        "bearer": "ghp_xxx",
                    }
                }
            })

    def test_github_with_env_interpolated_client_id(self, monkeypatch):
        monkeypatch.setenv("CI_GH_CLIENT_ID", "Iv1.from_env")
        out = parse_auth_providers({
            "providers": {
                "gh": {
                    "type": "github_device_flow",
                    "client_id": "${CI_GH_CLIENT_ID}",
                }
            }
        })
        assert out["gh"].client_id == "Iv1.from_env"


class TestParseAuthProvidersOkta:
    def test_minimal_okta_provider(self):
        out = parse_auth_providers({
            "providers": {
                "okta": {
                    "type": "okta_device_flow",
                    "issuer": "https://nike.okta.com/oauth2/default",
                    "client_id": "0oa.x",
                }
            }
        })
        spec = out["okta"]
        assert spec.type == "okta_device_flow"
        assert spec.issuer == "https://nike.okta.com/oauth2/default"
        assert spec.client_id == "0oa.x"

    def test_okta_with_membership_hint(self):
        out = parse_auth_providers({
            "providers": {
                "okta": {
                    "type": "okta_device_flow",
                    "issuer": "https://nike.okta.com/oauth2/default",
                    "client_id": "0oa.x",
                    "membership_hint": "Nike.uee.maria",
                }
            }
        })
        assert out["okta"].membership_hint == "Nike.uee.maria"

    def test_okta_requires_issuer(self):
        with pytest.raises(ConfigError, match="missing.*issuer"):
            parse_auth_providers({
                "providers": {
                    "okta": {
                        "type": "okta_device_flow",
                        "client_id": "0oa.x",
                    }
                }
            })

    def test_okta_rejects_non_url_issuer(self):
        with pytest.raises(ConfigError, match="https?://"):
            parse_auth_providers({
                "providers": {
                    "okta": {
                        "type": "okta_device_flow",
                        "issuer": "not-a-url",
                        "client_id": "0oa.x",
                    }
                }
            })

    def test_okta_membership_hint_blank_after_interp_treated_as_unset(
        self, monkeypatch,
    ):
        # An unset env var would raise; a SET env var with whitespace
        # / empty content normalises to None so the GUI doesn't show
        # "Membership required: " with a blank tail.
        monkeypatch.setenv("EMPTY_HINT", "   ")
        out = parse_auth_providers({
            "providers": {
                "okta": {
                    "type": "okta_device_flow",
                    "issuer": "https://x.okta.com/oauth2/default",
                    "client_id": "0oa.x",
                    "membership_hint": "${EMPTY_HINT}",
                }
            }
        })
        assert out["okta"].membership_hint is None


class TestParseAuthProvidersPassthrough:
    def test_passthrough_minimal(self):
        out = parse_auth_providers({
            "providers": {
                "legacy": {"type": "passthrough"}
            }
        })
        spec = out["legacy"]
        assert spec.type == "passthrough"
        assert spec.client_id is None
        assert spec.bearer is None

    def test_passthrough_rejects_extra_fields(self):
        with pytest.raises(ConfigError, match="unrecognised"):
            parse_auth_providers({
                "providers": {
                    "legacy": {
                        "type": "passthrough",
                        "client_id": "Iv1.x",
                    }
                }
            })


class TestParseAuthProvidersStatic:
    def test_static_minimal(self, monkeypatch):
        monkeypatch.setenv("STATIC_PAT", "ghp_secret_xxx")
        out = parse_auth_providers({
            "providers": {
                "ci": {
                    "type": "static",
                    "bearer": "${STATIC_PAT}",
                }
            }
        })
        assert out["ci"].bearer == "ghp_secret_xxx"

    def test_static_requires_bearer(self):
        with pytest.raises(ConfigError, match="missing.*bearer"):
            parse_auth_providers({
                "providers": {"ci": {"type": "static"}}
            })


class TestParseAuthProvidersDuplicates:
    def test_duplicate_name_case_insensitive(self):
        with pytest.raises(ConfigError, match="Duplicate auth provider"):
            parse_auth_providers({
                "providers": {
                    "gh": {"type": "passthrough"},
                    "GH": {"type": "passthrough"},
                }
            })

    def test_invalid_type(self):
        with pytest.raises(ConfigError, match="must be one of"):
            parse_auth_providers({
                "providers": {"x": {"type": "made_up_type"}}
            })

    def test_invalid_provider_name(self):
        with pytest.raises(ConfigError):
            parse_auth_providers({
                "providers": {
                    "with spaces": {"type": "passthrough"}
                }
            })


class TestAuthProviderSpecToStatus:
    def test_redacts_bearer(self, monkeypatch):
        monkeypatch.setenv("PAT", "ghp_secret")
        out = parse_auth_providers({
            "providers": {
                "ci": {"type": "static", "bearer": "${PAT}"}
            }
        })
        status = out["ci"].to_status(redacted=True)
        assert status["bearer"] == "***"
        # Unredacted form available for tests / privileged callers.
        unredacted = out["ci"].to_status(redacted=False)
        assert unredacted["bearer"] == "ghp_secret"

    def test_includes_membership_hint(self):
        out = parse_auth_providers({
            "providers": {
                "okta": {
                    "type": "okta_device_flow",
                    "issuer": "https://nike.okta.com/oauth2/default",
                    "client_id": "0oa.x",
                    "membership_hint": "Nike.uee.maria",
                }
            }
        })
        status = out["okta"].to_status()
        assert status["membership_hint"] == "Nike.uee.maria"

    def test_omits_unset_fields(self):
        out = parse_auth_providers({
            "providers": {
                "legacy": {"type": "passthrough"}
            }
        })
        status = out["legacy"].to_status()
        assert "client_id" not in status
        assert "bearer" not in status
        assert "membership_hint" not in status


# ── Per-backend auth.provider on parse_config ───────────────────────────


class TestPerBackendAuthProvider:
    def test_provider_only(self):
        specs, _ = parse_config({
            "mcpServers": {
                "github": {
                    "type": "streamable-http",
                    "url": "https://api.githubcopilot.com/mcp/",
                    "passthrough": True,
                    "auth": {"provider": "github_oauth_app"},
                }
            }
        })
        assert specs[0].auth_provider == "github_oauth_app"
        assert specs[0].auth_audience is None
        assert specs[0].auth_bearer is None

    def test_provider_with_audience(self):
        specs, _ = parse_config({
            "mcpServers": {
                "atlassian": {
                    "type": "streamable-http",
                    "url": "https://mcp-atlassian.nike.com/mcp",
                    "passthrough": True,
                    "auth": {
                        "provider": "nike_okta",
                        "audience": "api://atlassian-mcp",
                    },
                }
            }
        })
        assert specs[0].auth_provider == "nike_okta"
        assert specs[0].auth_audience == "api://atlassian-mcp"

    def test_provider_and_bearer_both_rejected(self):
        with pytest.raises(ConfigError, match="either 'bearer' OR 'provider'"):
            parse_config({
                "mcpServers": {
                    "github": {
                        "type": "streamable-http",
                        "url": "https://api.githubcopilot.com/mcp/",
                        "passthrough": True,
                        "auth": {
                            "provider": "github_oauth_app",
                            "bearer": "ghp_xxx",
                        },
                    }
                }
            })

    def test_audience_without_provider_rejected(self):
        with pytest.raises(ConfigError, match="audience.*alongside.*provider"):
            parse_config({
                "mcpServers": {
                    "x": {
                        "type": "streamable-http",
                        "url": "https://x/mcp",
                        "passthrough": True,
                        "auth": {"audience": "api://x"},
                    }
                }
            })

    def test_provider_requires_passthrough(self):
        with pytest.raises(ConfigError, match="auth.provider.*passthrough"):
            parse_config({
                "mcpServers": {
                    "github": {
                        "type": "streamable-http",
                        "url": "https://api.githubcopilot.com/mcp/",
                        "auth": {"provider": "github_oauth_app"},
                    }
                }
            })

    def test_provider_rejected_on_stdio(self):
        with pytest.raises(ConfigError, match="auth.provider.*passthrough HTTP"):
            parse_config({
                "mcpServers": {
                    "fs": {
                        "command": "uvx",
                        "args": ["server"],
                        "auth": {"provider": "github_oauth_app"},
                    }
                }
            })

    def test_provider_name_validated(self):
        with pytest.raises(ConfigError, match="invalid"):
            parse_config({
                "mcpServers": {
                    "github": {
                        "type": "streamable-http",
                        "url": "https://api.githubcopilot.com/mcp/",
                        "passthrough": True,
                        "auth": {"provider": "with spaces"},
                    }
                }
            })

    def test_legacy_bearer_still_works(self, monkeypatch):
        monkeypatch.setenv("PAT", "ghp_xxx")
        # Backward compatibility — auth.bearer untouched by PR 2.
        specs, _ = parse_config({
            "mcpServers": {
                "github": {
                    "type": "streamable-http",
                    "url": "https://api.githubcopilot.com/mcp/",
                    "passthrough": True,
                    "auth": {"bearer": "${PAT}"},
                }
            }
        })
        assert specs[0].auth_bearer == "ghp_xxx"
        assert specs[0].auth_provider is None


class TestServerSpecToStatusForProvider:
    def test_provider_appears_in_status(self):
        specs, _ = parse_config({
            "mcpServers": {
                "github": {
                    "type": "streamable-http",
                    "url": "https://api.githubcopilot.com/mcp/",
                    "passthrough": True,
                    "auth": {
                        "provider": "github_oauth_app",
                        "audience": "api://gh",
                    },
                }
            }
        })
        status = specs[0].to_status()
        assert status["auth"] == {
            "provider": "github_oauth_app",
            "audience": "api://gh",
        }

    def test_bearer_redacted_alongside_no_provider(self, monkeypatch):
        monkeypatch.setenv("PAT", "ghp_xxx")
        specs, _ = parse_config({
            "mcpServers": {
                "github": {
                    "type": "streamable-http",
                    "url": "https://api.githubcopilot.com/mcp/",
                    "passthrough": True,
                    "auth": {"bearer": "${PAT}"},
                }
            }
        })
        status = specs[0].to_status()
        assert status["auth"] == {"bearer": "***"}


# ── validate_provider_references ────────────────────────────────────────


class TestValidateProviderReferences:
    def test_no_references_no_validation(self):
        specs, _ = parse_config({
            "mcpServers": {
                "fs": {"command": "uvx", "args": ["server"]},
            }
        })
        # No references + empty provider set → no error.
        validate_provider_references(specs, {})

    def test_resolved_reference_passes(self):
        specs, _ = parse_config({
            "mcpServers": {
                "github": {
                    "type": "streamable-http",
                    "url": "https://api.githubcopilot.com/mcp/",
                    "passthrough": True,
                    "auth": {"provider": "github_oauth_app"},
                }
            }
        })
        providers = parse_auth_providers({
            "providers": {
                "github_oauth_app": {
                    "type": "github_device_flow",
                    "client_id": "Iv1.x",
                }
            }
        })
        validate_provider_references(specs, providers)  # No error.

    def test_dangling_reference_raises(self):
        specs, _ = parse_config({
            "mcpServers": {
                "github": {
                    "type": "streamable-http",
                    "url": "https://api.githubcopilot.com/mcp/",
                    "passthrough": True,
                    "auth": {"provider": "missing_provider"},
                }
            }
        })
        with pytest.raises(ConfigError, match="missing_provider"):
            validate_provider_references(specs, {})

    def test_dangling_reference_lists_available(self):
        specs, _ = parse_config({
            "mcpServers": {
                "github": {
                    "type": "streamable-http",
                    "url": "https://api.githubcopilot.com/mcp/",
                    "passthrough": True,
                    "auth": {"provider": "typo"},
                }
            }
        })
        providers = parse_auth_providers({
            "providers": {
                "github_oauth_app": {
                    "type": "github_device_flow",
                    "client_id": "Iv1.x",
                }
            }
        })
        with pytest.raises(ConfigError, match="github_oauth_app"):
            validate_provider_references(specs, providers)


# ── factory.build_provider ──────────────────────────────────────────────


class TestBuildProvider:
    def test_builds_passthrough(self):
        spec = AuthProviderSpec(name="legacy", type="passthrough")
        provider = build_provider(spec, store=None)
        assert isinstance(provider, PassthroughProvider)
        assert provider.name == "legacy"

    def test_builds_static(self):
        spec = AuthProviderSpec(
            name="ci", type="static", bearer="ghp_secret",
        )
        provider = build_provider(spec, store=None)
        assert isinstance(provider, StaticBearerProvider)
        assert provider.name == "ci"

    def test_github_factory_requires_store(self):
        # Github factory needs the encrypted auth store for per-user
        # token persistence. Calling build_provider with store=None
        # surfaces ProviderTypeUnavailable so the manager falls back
        # to "unavailable" status rather than crashing.
        spec = AuthProviderSpec(
            name="gh", type="github_device_flow", client_id="Iv1.x",
        )
        with pytest.raises(ProviderTypeUnavailable):
            build_provider(spec, store=None)

    def test_okta_factory_requires_store(self):
        # Same as github: the okta factory needs the encrypted auth
        # store. Calling build_provider with store=None surfaces
        # ProviderTypeUnavailable so the manager falls back to
        # "unavailable" status rather than crashing.
        spec = AuthProviderSpec(
            name="okta",
            type="okta_device_flow",
            issuer="https://nike.okta.com/oauth2/default",
            client_id="0oa.x",
        )
        with pytest.raises(ProviderTypeUnavailable):
            build_provider(spec, store=None)
