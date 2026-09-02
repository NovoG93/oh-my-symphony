"""Documentation contracts for web authorization, Runs, and MCP deployment.

These checks intentionally assert policy concepts and safety invariants rather
than exact prose.  They keep the operator documentation aligned with the
runtime policy while allowing the Korean translation to use natural wording.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README_PATHS = (ROOT / "README.md", ROOT / "README.ko.md")
ACTIVE_DOC_PATHS = README_PATHS + (ROOT / "deploy" / "README.md",)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_both_readmes_document_the_three_web_modes_and_credentials() -> None:
    required = (
        "token",
        "disabled",
        "capabilities",
        "SYMPHONY_API_AUTH_MODE",
        "SYMPHONY_TRUSTED_ORIGINS",
        "SYMPHONY_REMOTE_OPERATOR_CAPABILITIES",
        "SYMPHONY_API_TOKEN",
        "SYMPHONY_API_TOKEN_FILE",
    )
    for path in README_PATHS:
        text = _read(path)
        missing = [term for term in required if term not in text]
        assert not missing, f"{path.name} is missing web-policy terms: {missing}"


def test_both_readmes_document_safe_token_minting() -> None:
    for path in README_PATHS:
        text = _read(path)
        assert "openssl rand -hex 32" in text, path.name
        assert re.search(r"chmod\s+0?600", text), path.name
        assert re.search(r"chmod\s+0?700|mode\s+0?700|directory.{0,40}0?700", text, re.I), path.name
        assert re.search(r"commit|log", text, re.I), path.name


def test_active_docs_do_not_preserve_stale_auth_guidance() -> None:
    text = "\n".join(_read(path) for path in ACTIVE_DOC_PATHS)
    assert "all Runs endpoints accept loopback clients only" not in text
    assert "history CLI/API는 아직 없다" not in text
    assert not re.search(r"\?token=", text, re.I)


def test_both_readmes_cover_exact_origins_and_websocket_tickets() -> None:
    for path in README_PATHS:
        text = _read(path)
        assert re.search(r"exact.{0,80}origin|정확한.{0,80}origin", text, re.I | re.S), path.name
        assert re.search(r"scheme|스킴", text, re.I) and re.search(r"host|호스트", text, re.I), path.name
        assert re.search(r"WebSocket.{0,180}(ticket|티켓)", text, re.I | re.S), path.name
        assert re.search(r"single-use|일회용", text, re.I), path.name


def test_mcp_guide_separates_credentials_and_covers_all_upstream_modes() -> None:
    text = _read(ROOT / "deploy" / "README.md")
    for mode in ("token", "disabled", "capabilities"):
        assert mode in text
    for variable in (
        "SYMPHONY_MCP_TOKEN",
        "SYMPHONY_MCP_TOKEN_FILE",
        "SYMPHONY_API_TOKEN",
        "SYMPHONY_API_TOKEN_FILE",
        "SYMPHONY_MCP_ALLOWED_PROJECTS",
        "SYMPHONY_MCP_ALLOW_CONTROL",
    ):
        assert variable in text
    assert re.search(r"caller|client", text, re.I)
    assert re.search(r"upstream|Symphony", text, re.I)
    assert re.search(r"drop[- ]?in", text, re.I)
    assert re.search(
        r"\[Service\].{0,240}SYMPHONY_API_TOKEN_FILE", text, re.I | re.S
    )
    assert "initialize" in text and "tool" in text
    assert "bearer_token_env_var" in text
    assert re.search(r"chmod\s+600|mode[- ]?0600", text, re.I)


def test_security_history_is_explicitly_superseded() -> None:
    changelog = _read(ROOT / "CHANGELOG.md")
    assert re.search(r"Unreleased.{0,500}supersed", changelog, re.I | re.S)
    assert re.search(r"wildcard.{0,180}bare[- ]host|bare[- ]host.{0,180}wildcard", changelog, re.I | re.S)
    assert "0.20.1" in changelog and "historical" in changelog.lower()

    adr = _read(ROOT / "docs" / "adr" / "0003-bounded-run-attempt-diagnostics.md")
    assert re.search(r"superseded", adr, re.I)
    assert "loopback-only" in adr
    assert all(mode in adr for mode in ("token", "disabled", "capabilities"))


def test_changed_documentation_links_resolve() -> None:
    link_pattern = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    for path in (*ACTIVE_DOC_PATHS, ROOT / "CHANGELOG.md", ROOT / "docs/adr/0003-bounded-run-attempt-diagnostics.md"):
        for match in link_pattern.finditer(_read(path)):
            target = match.group(1).strip().split("#", 1)[0]
            if not target or target.startswith(("http:", "https:", "mailto:")):
                continue
            assert (path.parent / target).exists(), f"broken local link in {path}: {target}"
