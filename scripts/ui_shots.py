#!/usr/bin/env python3
"""Screenshot every board page at mobile / tablet / desktop widths.

Used to eyeball the responsive layout and the i18n pass. Writes PNGs into
/tmp/symphony-shots and prints any console errors the page produced.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8899"
LANG = sys.argv[2] if len(sys.argv) > 2 else "en"
OUT = Path("/tmp/symphony-shots")

VIEWPORTS = {
    "desktop": (1440, 900),
    "tablet": (834, 1112),
    "mobile": (390, 844),
}
ROUTES = ["board", "stats", "workflow", "git", "chat", "settings"]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome")
        except Exception:
            browser = pw.chromium.launch()

        for name, (width, height) in VIEWPORTS.items():
            context = browser.new_context(viewport={"width": width, "height": height})
            page = context.new_page()
            page.on("pageerror", lambda e: errors.append(f"[{name}] pageerror: {e}"))
            page.on(
                "console",
                lambda m: errors.append(f"[{name}] console.{m.type}: {m.text}")
                if m.type == "error"
                else None,
            )
            page.goto(BASE, wait_until="networkidle")
            if LANG != "en":
                page.evaluate(f"window.i18n.setLang('{LANG}')")
                page.wait_for_timeout(300)
            for route in ROUTES:
                page.goto(f"{BASE}/#/{route}", wait_until="networkidle")
                page.wait_for_timeout(700)
                page.screenshot(path=str(OUT / f"{LANG}-{name}-{route}.png"))
            context.close()
        browser.close()

    for line in errors:
        print(line)
    print(f"screenshots written to {OUT} ({LANG})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
