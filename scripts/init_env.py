#!/usr/bin/env python3
"""zelosMCP environment helper.

Walks the user through the most common Makefile variables and writes a
working ``.env`` file. Optionally writes ``configs/user-zelosmcp.json``
(a subset of ``configs/default-zelosmcp.json``) when the user opts out
of any default backend.

Stdlib only — no extra dependencies. Auto-detects PLATFORM,
DOCKER_SOCK_FILE, and corp-cert presence so the prompts default to
something sensible on the current host.

Usage:
    make init-env             # canonical
    python3 scripts/init_env.py
    python3 scripts/init_env.py --force   # overwrite .env without asking
"""
from __future__ import annotations

import argparse
import json
import platform as plat
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
DEFAULT_CONFIG = REPO_ROOT / "configs" / "default-zelosmcp.json"
USER_CONFIG = REPO_ROOT / "configs" / "user-zelosmcp.json"
AUTH_PROVIDERS_CONFIG = REPO_ROOT / "configs" / "auth-providers.json"


# ── Auto-detection ──────────────────────────────────────────────────────


def detect_platform() -> str:
    machine = plat.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "linux/arm64"
    return "linux/amd64"


def detect_docker_sock() -> str:
    """Pick /var/run/docker.sock when present, fall back to ~/.rd/docker.sock."""
    if Path("/var/run/docker.sock").exists():
        return "/var/run/docker.sock"
    rd = Path.home() / ".rd" / "docker.sock"
    if rd.exists():
        return str(rd)
    return "/var/run/docker.sock"


def detect_corp_cert(cn: str = "Nike Root Authority NG") -> bool:
    """Return True when ``cn`` is in the macOS keychain."""
    if sys.platform != "darwin":
        return False
    try:
        result = subprocess.run(
            ["security", "find-certificate", "-c", cn],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ── Prompt helpers ──────────────────────────────────────────────────────


def ask(prompt: str, default: str) -> str:
    answer = input(f"{prompt} [{default}]: ").strip()
    return answer or default


def ask_yes_no(prompt: str, default: bool) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"{prompt} {suffix} ").strip().lower()
    if not answer:
        return default
    return answer.startswith("y")


def ask_choice(prompt: str, options: dict[str, str], default: str) -> str:
    """Prompt with single-key choices. ``options`` maps key -> description.

    The default key is upper-cased in the prompt; others lower-cased.
    Returns the selected key.
    """
    keys = list(options.keys())
    pretty = "/".join(k.upper() if k == default else k for k in keys)
    while True:
        for k, desc in options.items():
            print(f"  ({k}) {desc}")
        answer = input(f"{prompt} [{pretty}]: ").strip().lower()
        if not answer:
            return default
        if answer in options:
            return answer
        print(f"  Please pick one of: {', '.join(keys)}")


# ── Main ────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing .env without asking",
    )
    args = parser.parse_args()

    if ENV_FILE.exists() and not args.force:
        if not ask_yes_no(
            f"{ENV_FILE.relative_to(REPO_ROOT)} already exists. Overwrite?",
            False,
        ):
            print("Aborted.")
            return 1

    print(
        "\nzelosMCP setup wizard.\n"
        "Press Enter to accept the default shown in brackets.\n"
    )

    home = str(Path.home())

    # 1) Source tree
    user_data = ask(
        "Source code directory to expose (USER_DATA_ROOT)",
        f"{home}/workspace",
    )

    # 2) HTTP surface
    port = ask("HTTP port (ZELOSMCP_PORT)", "8000")
    bind = ask(
        "Bind address — 127.0.0.1 (this Mac only) or 0.0.0.0 (LAN access) "
        "(ZELOSMCP_BIND_ADDR)",
        "127.0.0.1",
    )

    # 3) Dockerfile (auto-default driven by corp-cert detection)
    has_corp_cert = detect_corp_cert()
    if has_corp_cert:
        print(
            "(detected corporate root cert in macOS keychain — "
            "defaulting DOCKERFILE to docker-tools/Dockerfile)"
        )
        dockerfile_default = "docker-tools/Dockerfile"
    else:
        dockerfile_default = "Dockerfile"
    dockerfile = ask(
        "Dockerfile (community: 'Dockerfile'; corp-cert-aware: "
        "'docker-tools/Dockerfile') (DOCKERFILE)",
        dockerfile_default,
    )

    # 3a) Conditional: corp cert CN if corp Dockerfile
    cert_name = ""
    if "docker-tools" in dockerfile:
        cert_name = ask(
            "Corporate root CA cert CN — exported from your macOS keychain "
            "(CORP_ROOT_AUTHORITY_CERT_NAME)",
            "Nike Root Authority NG",
        )

    # 4) Kubernetes
    enable_k8s = ask_yes_no("Enable kubernetes access?", True)
    kube_config = ""
    if enable_k8s:
        kube_config = ask(
            "Path to kubeconfig (KUBERNETES_CONFIG_FILE)",
            f"{home}/.kube/config",
        )

    # 5) Docker
    enable_docker = ask_yes_no("Enable docker access?", True)
    docker_sock = ""
    if enable_docker:
        docker_sock = ask(
            "Docker daemon socket (DOCKER_SOCK_FILE)",
            detect_docker_sock(),
        )

    # 6) Cursor rule scope
    rule_scope = ask_choice(
        "Cursor rule scope: per-project or all your Cursor projects?",
        {
            "a": "this project only (.cursor/rules/zelosmcp.mdc)",
            "b": "all my Cursor projects ($HOME/.cursor/rules/zelosmcp.mdc)",
        },
        default="a",
    )
    if rule_scope == "a":
        rule_file = ".cursor/rules/zelosmcp.mdc"
    else:
        rule_file = f"{home}/.cursor/rules/zelosmcp.mdc"

    # 7) Cursor rule access mode
    rw_default = ask_yes_no(
        "Allow the agent to call tools that mutate state (write files, "
        "create containers, etc.)?",
        True,
    )
    rule_access = "read-write" if rw_default else "read-only"

    # 8) Decide which config file `make load` should POST.
    if enable_k8s and enable_docker:
        config_file = "configs/default-zelosmcp.json"
    else:
        with DEFAULT_CONFIG.open() as fh:
            cfg = json.load(fh)
        servers = cfg.get("mcpServers", {})
        if not enable_k8s:
            servers.pop("kubernetes", None)
        if not enable_docker:
            servers.pop("docker", None)
        USER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        with USER_CONFIG.open("w") as fh:
            json.dump({"mcpServers": servers}, fh, indent=2)
            fh.write("\n")
        config_file = "configs/user-zelosmcp.json"
        print(
            f"  wrote {USER_CONFIG.relative_to(REPO_ROOT)} "
            f"(subset of default config, {len(servers)} backend"
            f"{'s' if len(servers) != 1 else ''})"
        )

    # 9) Auth providers (configs/auth-providers.json).
    print(
        "\nAuth providers — broker mode for OAuth-protected backends.\n"
        "  - GitHub: device flow against github.com (Cursor is PAT-only\n"
        "    for the GitHub MCP; this gives you a GUI-driven alternative).\n"
        "  - Okta: Authorization Code + PKCE against your Okta tenant.\n"
        "    Register a Native app with Authorization Code + Refresh Token\n"
        "    grants and a redirect URI back to zelosMCP. See\n"
        "    docs/oauth-passthrough.md.\n"
    )
    providers_block: dict = {}
    auth_env: dict[str, str] = {}

    enable_github_provider = ask_yes_no(
        "Configure the github_oauth_app provider?", True,
    )
    if enable_github_provider:
        gh_client_id = ask(
            "GitHub OAuth App client_id (ZELOSMCP_GITHUB_CLIENT_ID)",
            "",
        )
        if gh_client_id:
            auth_env["ZELOSMCP_GITHUB_CLIENT_ID"] = gh_client_id
        else:
            print(
                "  WARNING: empty client_id; the provider will fail to "
                "load until you set ZELOSMCP_GITHUB_CLIENT_ID in .env."
            )
        providers_block["github_oauth_app"] = {
            "type": "github_device_flow",
            "client_id": "${ZELOSMCP_GITHUB_CLIENT_ID}",
            "scopes": [
                "repo",
                "read:org",
                "user:email",
                "gist",
                "workflow",
            ],
        }

    enable_okta_provider = ask_yes_no(
        "Configure the nike_okta provider?", False,
    )
    if enable_okta_provider:
        okta_issuer = ask(
            "Okta authorization server issuer URL "
            "(e.g. https://nike.okta.com/oauth2/default) "
            "(ZELOSMCP_OKTA_ISSUER)",
            "",
        )
        okta_client_id = ask(
            "Okta App client_id (ZELOSMCP_OKTA_CLIENT_ID)",
            "",
        )
        okta_redirect_uri = ask(
            "Okta redirect URI (ZELOSMCP_OKTA_REDIRECT_URI)",
            "http://localhost:8000/api/auth/nike_okta/callback",
        )
        okta_membership_hint = ask(
            "Okta authorized-group display string for the Connections "
            "GUI (ZELOSMCP_OKTA_MEMBERSHIP_HINT)",
            "Nike.uee.maria",
        )
        if okta_issuer:
            auth_env["ZELOSMCP_OKTA_ISSUER"] = okta_issuer
        if okta_client_id:
            auth_env["ZELOSMCP_OKTA_CLIENT_ID"] = okta_client_id
        if okta_redirect_uri:
            auth_env["ZELOSMCP_OKTA_REDIRECT_URI"] = okta_redirect_uri
        if okta_membership_hint:
            auth_env["ZELOSMCP_OKTA_MEMBERSHIP_HINT"] = okta_membership_hint
        providers_block["nike_okta"] = {
            "type": "okta_authorization_code",
            "issuer": "${ZELOSMCP_OKTA_ISSUER}",
            "client_id": "${ZELOSMCP_OKTA_CLIENT_ID}",
            "redirect_uri": "${ZELOSMCP_OKTA_REDIRECT_URI}",
            "scopes": ["openid", "profile", "email"],
            "membership_hint": "${ZELOSMCP_OKTA_MEMBERSHIP_HINT}",
        }

    if providers_block:
        AUTH_PROVIDERS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        with AUTH_PROVIDERS_CONFIG.open("w") as fh:
            json.dump({"providers": providers_block}, fh, indent=2)
            fh.write("\n")
        print(
            f"  wrote {AUTH_PROVIDERS_CONFIG.relative_to(REPO_ROOT)} "
            f"({len(providers_block)} provider"
            f"{'s' if len(providers_block) != 1 else ''})"
        )
    else:
        print(
            "  no providers configured — broker-mode backends will fail "
            "to load. Edit configs/auth-providers.json by hand or rerun "
            "this wizard."
        )

    # 10) Auto-detect platform.
    platform_value = detect_platform()
    print(f"(detected platform: {platform_value})")

    # ── Compose .env ────────────────────────────────────────────────────
    lines = [
        "# Generated by scripts/init_env.py — re-run anytime to regenerate.",
        "# Override any value manually; the Makefile picks them up via "
        "`-include .env`.",
        "",
        f"USER_DATA_ROOT={user_data}",
        f"ZELOSMCP_PORT={port}",
        f"ZELOSMCP_BIND_ADDR={bind}",
        f"DOCKERFILE={dockerfile}",
        f"PLATFORM={platform_value}",
        f"ZELOSMCP_CONFIG={config_file}",
        f"ZELOSMCP_RULE_FILE={rule_file}",
        f"ZELOSMCP_RULE_ACCESS={rule_access}",
    ]
    if cert_name:
        lines.append(f"CORP_ROOT_AUTHORITY_CERT_NAME={cert_name}")
    if enable_k8s:
        lines.append(f"KUBERNETES_CONFIG_FILE={kube_config}")
    if enable_docker:
        lines.append(f"DOCKER_SOCK_FILE={docker_sock}")
    if providers_block:
        # Tell `make up` to mount the providers file we just wrote and
        # `make load` to POST it after the mcpServers config.
        lines.append(
            f"ZELOSMCP_AUTH_PROVIDERS_FILE={AUTH_PROVIDERS_CONFIG}"
        )
    for env_name, env_val in auth_env.items():
        lines.append(f"{env_name}={env_val}")
    lines.append("")

    ENV_FILE.write_text("\n".join(lines))
    print(f"\nWrote {ENV_FILE.relative_to(REPO_ROOT)}.")
    print("Next: make up")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        sys.exit(130)
