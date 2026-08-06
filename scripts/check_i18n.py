#!/usr/bin/env python3
"""Consistency check for the board SPA translations.

Fails when a t('key') call has no English entry, when a language is missing a
key English has, or when a dictionary entry is never used. English is the
source of truth, so an extra key in another language is also an error.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DICT_RE = re.compile(r"const (\w+) = \{(.*?)\n  \};", re.S)
ENTRY_RE = re.compile(r"^\s*'([^']+)':\s*'((?:[^'\\]|\\.)*)',\s*$", re.M)
# Keys are not always the first argument — `t(cond ? 'a.b' : 'a.c')` is valid —
# so collect every literal shaped like a dictionary key instead of parsing calls.
CALL_RE = re.compile(r"'([a-z][a-zA-Z0-9]*\.[a-zA-Z0-9]+)'")

# localStorage keys share the dotted shape but are not translations.
NON_KEY_PREFIXES = ("symphony.",)
STATIC_RE = re.compile(r'data-i18n(?:-attr)?="([^"]+)"')


def load_dicts(path: Path) -> dict[str, dict[str, str]]:
    source = path.read_text(encoding="utf-8")
    out: dict[str, dict[str, str]] = {}
    for name, block in DICT_RE.findall(source):
        if name in ("en", "ko"):
            out[name] = dict(ENTRY_RE.findall(block))
    return out


def main() -> int:
    static_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("src/symphony/web/static")
    dicts = load_dicts(static_dir / "i18n.js")
    if "en" not in dicts:
        print("FAIL: no English dictionary found")
        return 1
    en = dicts["en"]

    used: set[str] = {
        key
        for key in CALL_RE.findall((static_dir / "app.js").read_text(encoding="utf-8"))
        if not key.startswith(NON_KEY_PREFIXES)
    }
    for attr in STATIC_RE.findall((static_dir / "index.html").read_text(encoding="utf-8")):
        for pair in attr.split(","):
            used.add(pair.split(":")[-1].strip())

    problems = 0

    missing = sorted(used - set(en))
    if missing:
        problems += len(missing)
        print(f"FAIL: {len(missing)} key(s) used but not defined in English:")
        for key in missing:
            print(f"  {key}")

    unused = sorted(set(en) - used)
    if unused:
        problems += len(unused)
        print(f"FAIL: {len(unused)} English key(s) defined but never used:")
        for key in unused:
            print(f"  {key}")

    for lang, table in dicts.items():
        if lang == "en":
            continue
        absent = sorted(set(en) - set(table))
        extra = sorted(set(table) - set(en))
        if absent:
            problems += len(absent)
            print(f"FAIL: {lang} is missing {len(absent)} key(s):")
            for key in absent:
                print(f"  {key}")
        if extra:
            problems += len(extra)
            print(f"FAIL: {lang} has {len(extra)} key(s) not in English:")
            for key in extra:
                print(f"  {key}")

    if problems:
        print(f"\n{problems} problem(s)")
        return 1

    print(f"OK: {len(en)} keys, languages={sorted(dicts)}, all used and translated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
