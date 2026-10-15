# SDLC config

kit: /Users/danny/.pi/agent/skills/sdlc-kit   # re-point this if the kit is moved or cloned elsewhere
kit_version: v0.8.0   # kit version this project was seeded with

# Real commands agents must use for proof (fill these in — brownfield: copy from CI/Makefile)

# AGENTS: if a command below is empty when you need it, STOP and ask the human to fill it in

build: python3.12 -m pip wheel . --no-deps --wheel-dir /tmp/oh-my-symphony-wheel
test: env -u SYMPHONY_LANG bash -c '.venv/bin/python -m pytest -q -n auto -m "not timing" --cov=src/symphony --cov-report= && .venv/bin/python -m pytest -q -n0 -m timing --cov=src/symphony --cov-append --cov-report=term --cov-fail-under=80'
lint: .venv/bin/python -m ruff check src tests && .venv/bin/python scripts/check_i18n.py && .venv/bin/symphony-pyright
run: .venv/bin/python -m symphony --help

# Human gates: intent only; subsequent gates auto-approve after required reviews.
# Set on the owner's explicit correction, 2026-10-04: "keep intent only".
lazymode: 3
