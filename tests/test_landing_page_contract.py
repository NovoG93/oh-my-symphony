"""The public landing page must not advertise a version we do not ship.

`docs/index.html` is served on GitHub Pages, so its version badge is an
outward-facing claim. It has drifted before (see the 0.9.1 "Landing page
version sync" entry in CHANGELOG.md); this test makes the next bump fail
loudly instead of shipping a stale number.
"""

from __future__ import annotations

import re
from pathlib import Path

import symphony

REPO_ROOT = Path(__file__).resolve().parent.parent
LANDING = REPO_ROOT / "docs" / "index.html"


def test_landing_page_badge_matches_the_shipped_version() -> None:
    html = LANDING.read_text(encoding="utf-8")
    badges = re.findall(r"v(\d+\.\d+\.\d+)", html)
    assert badges, "no version badge found in docs/index.html"
    stale = sorted({badge for badge in badges if badge != symphony.__version__})
    assert not stale, (
        f"docs/index.html advertises {stale} but this tree ships "
        f"{symphony.__version__} — update the landing badge"
    )


def test_changelog_documents_the_shipped_version() -> None:
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{symphony.__version__}]" in changelog, (
        f"CHANGELOG.md has no section for {symphony.__version__}"
    )


def test_pyproject_and_package_version_stay_in_lockstep() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert match is not None, "pyproject.toml has no version"
    assert match.group(1) == symphony.__version__
