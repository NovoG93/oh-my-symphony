from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time

import pytest

from symphony.pm.models import WbsDependency, WbsTask
from symphony.pm.wbs import parse_wbs, validate_wbs

# Wall-clock budgets: CPU contention from parallel workers invalidates them.
pytestmark = pytest.mark.timing


def test_reference_5000_node_validation_p95_budget() -> None:
    tasks = tuple(
        WbsTask(
            f"T-{index:08d}", None, index, "Feature", f"Task {index}",
            dependencies=(WbsDependency(f"T-{index - 1:08d}", "requires_code"),) if index else (),
        )
        for index in range(5000)
    )
    for _ in range(3):
        assert validate_wbs(tasks) == ()
    samples = []
    for _ in range(30):
        started = time.perf_counter()
        assert validate_wbs(tasks) == ()
        samples.append(time.perf_counter() - started)
    ordered = sorted(samples)
    p95 = ordered[int(len(ordered) * 0.95) - 1]
    assert p95 < 2.0, {
        "samples": samples,
        "p50": statistics.median(samples),
        "p95": p95,
        "max": max(samples),
        "range": max(samples) - min(samples),
    }


def _reference_1000_task_parse_samples() -> list[float]:
    fence = "`" * 3
    source = ["# Plan\n<!-- symphony-wbs-format: 1 -->\n"]
    for index in range(1000):
        task_id = f"T-{index:08d}"
        source.append(
            f"""<!-- symphony-task:{task_id} -->
- [ ] Task {index}
{fence}yaml symphony-task
id: {task_id}
parent_id: null
order: {index}
type: Backend
depends_on: []
{fence}
### Scope
Implement task {index}.
### Acceptance criteria
- Done.
### Verification
- Test.
### Integration impact
None.
<!-- /symphony-task -->
"""
        )
    document = "".join(source)
    samples = []
    for _ in range(10):
        started = time.perf_counter()
        parsed = parse_wbs(document)
        samples.append(time.perf_counter() - started)
        assert len(parsed.tasks) == 1000
        assert parsed.findings == ()
    return samples


def test_reference_1000_task_source_parse_stays_below_event_loop_budget() -> None:
    # Coverage tracing adds roughly 160 ms to this CPU-bound parser on the
    # reference host and can turn scheduler noise into a false release failure.
    # Measure production execution in a fresh, explicitly untraced interpreter;
    # the 1,000-task workload, ten samples, and 250 ms maximum stay unchanged.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key != "COVERAGE_PROCESS_START" and not key.startswith("COV_CORE_")
    }
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, sys; "
                "from tests.test_pm_performance import "
                "_reference_1000_task_parse_samples as measure; "
                "assert sys.gettrace() is None; "
                "print(json.dumps(measure()))"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        env=environment,
    )
    assert probe.returncode == 0, probe.stderr
    samples = json.loads(probe.stdout)
    assert max(samples) < 0.25, {"samples": samples, "max": max(samples)}
