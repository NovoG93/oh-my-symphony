"""Capability skips that become failures when PM proof is required."""

from __future__ import annotations

import os
from typing import NoReturn

import pytest


def skip_or_fail(reason: str, *, flag: str) -> NoReturn:
    if os.environ.get(flag) == "1":
        pytest.fail(reason)
    pytest.skip(reason)
