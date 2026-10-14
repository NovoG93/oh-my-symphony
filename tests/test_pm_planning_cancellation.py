"""Deterministic planning lifetime tests using the real client and memory-only RPC."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import symphony.pm.planning as planning
from symphony.pm import codex_compatibility
from symphony.pm.models import PmError
from symphony.pm.planning import PmPlanningClient


class MemoryRuntime:
    def __init__(self, stage: str) -> None:
        self.stage = stage
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.admission_cancelled = False
        self.methods: list[str] = []
        self.jobs: list[asyncio.Task] = []
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.process = SimpleNamespace(pid=424242, returncode=0, stdout=self.stdout,
                                       stderr=self.stderr, stdin=self)
        self.last_method = ""
        self.spawns = 0
        self.patch = "safe patch"

    async def pause(self, stage: str) -> None:
        if self.stage == stage:
            self.entered.set()
            await self.release.wait()

    async def admit(self, *_args, **_kwargs):
        try:
            await self.pause("admission")
        except asyncio.CancelledError:
            self.admission_cancelled = True
            raise
        return SimpleNamespace(reason=None, executable="memory-codex")

    async def spawn(self, *_args, **_kwargs):
        await self.pause("spawn")
        self.spawns += 1
        return self.process

    def write(self, data: bytes) -> None:
        message = json.loads(data)
        self.last_method = message.get("method", "")
        self.methods.append(self.last_method)
        if "id" in message:
            self.jobs.append(asyncio.create_task(self.respond(message)))

    async def drain(self) -> None:
        if self.last_method == "initialize":
            await self.pause("request_drain")
        elif self.last_method == "initialized":
            await self.pause("notify_drain")

    async def respond(self, message: dict) -> None:
        method = message["method"]
        await self.pause(method)
        result = {"thread": {"id": "memory-thread"}} if method == "thread/start" else (
            {"turn": {"id": "memory-turn"}} if method == "turn/start" else {})
        self.stdout.feed_data((json.dumps({"id": message["id"], "result": result}) + "\n").encode())
        if method == "turn/start":
            await self.pause("turn_wait")
            text = json.dumps({"target": "planning.md", "proposal_patch": self.patch,
                               "write_or_dispatch_performed": False, "blocked_instruction": ""})
            for event in [
                {"method": "item/completed", "params": {"threadId": "memory-thread", "turnId": "memory-turn", "item": {"type": "agentMessage", "text": text}}},
                {"method": "turn/completed", "params": {"threadId": "memory-thread", "turn": {"id": "memory-turn", "status": "completed"}}},
            ]:
                self.stdout.feed_data((json.dumps(event) + "\n").encode())

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(codex_compatibility, "admit_codex", self.admit)
        monkeypatch.setattr(planning.asyncio, "create_subprocess_exec", self.spawn)
        monkeypatch.setattr(planning, "process_identity", lambda _pid: "memory-owned")
        monkeypatch.setattr(planning, "process_group_exists", lambda _pid: False)
        monkeypatch.setattr(planning, "_owned_group_signal_is_safe", lambda *_args: True)

    async def close(self) -> None:
        self.release.set()
        for job in self.jobs:
            if not job.done():
                job.cancel()
        await asyncio.gather(*self.jobs, return_exceptions=True)


@pytest.mark.asyncio
async def test_stop_settles_rpc_and_turn_waiters_without_stdout_eof(monkeypatch):
    runtime = MemoryRuntime("initialize")
    runtime.install(monkeypatch)
    client = PmPlanningClient(timeout_seconds=240)
    client.process = runtime.process
    client.reader_task = asyncio.create_task(client._read_stdout())
    client.stderr_task = asyncio.create_task(client._read_stderr())
    request = asyncio.create_task(client.request("initialize", {}, timeout=240))
    turn = asyncio.create_task(client._wait_turn("thread", "turn", deadline=asyncio.get_running_loop().time() + 240))
    try:
        await runtime.entered.wait()
        await client.stop()
        results = await asyncio.wait_for(asyncio.gather(request, turn, return_exceptions=True), 1)
        assert all(isinstance(result, PmError) and result.code == "planning_failed" for result in results)
        assert not client.pending
        assert client.reader_task.done() and client.stderr_task.done()
    finally:
        for task in (request, turn):
            task.cancel()
        await asyncio.gather(request, turn, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["admission", "spawn", "initialize", "thread/start", "turn/start", "turn_wait", "request_drain", "notify_drain"])
async def test_cancel_joins_owned_proposal_at_each_boundary(tmp_path: Path, monkeypatch, stage):
    runtime = MemoryRuntime(stage)
    runtime.install(monkeypatch)
    auth = tmp_path / "dummy-auth"
    auth.write_text("{}")
    client = PmPlanningClient(auth_file=auth, timeout_seconds=240)
    request = asyncio.create_task(client.propose(tmp_path, "snapshot", "review"))
    try:
        await asyncio.wait_for(runtime.entered.wait(), 1)
        cancels = asyncio.gather(client.cancel_proposal(), client.cancel_proposal())
        await asyncio.sleep(0)
        runtime.release.set()
        await asyncio.wait_for(cancels, 2)
        with pytest.raises(PmError) as error:
            await asyncio.wait_for(request, 1)
        assert error.value.code == "operation_cancelled"
        assert client.process is None and not client.cleanup_uncertain
        assert not client.pending
        if stage in {"admission", "spawn", "initialize", "thread/start", "request_drain", "notify_drain"}:
            assert "turn/start" not in runtime.methods
        assert not runtime.admission_cancelled
        await client.cancel_proposal()
        with pytest.raises(PmError) as repeated:
            await client.propose(tmp_path, "snapshot", "review")
        assert repeated.value.code == "operation_cancelled"
    finally:
        runtime.release.set()
        if not request.done():
            request.cancel()
        await asyncio.gather(request, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
async def test_caller_cancellation_joins_admission_before_returning(tmp_path, monkeypatch):
    runtime = MemoryRuntime("admission")
    runtime.install(monkeypatch)
    auth = tmp_path / "dummy-auth"
    auth.write_text("{}")
    client = PmPlanningClient(auth_file=auth, timeout_seconds=240)
    request = asyncio.create_task(client.propose(tmp_path, "snapshot", "review"))
    try:
        await runtime.entered.wait()
        request.cancel()
        await asyncio.sleep(0)
        assert not request.done(), "caller returned before its owned startup finished"
        runtime.release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(request, 1)
        assert not runtime.admission_cancelled
        assert runtime.spawns == 0 and client.process is None
    finally:
        runtime.release.set()
        await asyncio.gather(request, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("eof", [False, True])
async def test_reader_failure_settles_pending_rpc_and_terminal(monkeypatch, eof):
    runtime = MemoryRuntime("initialize")
    runtime.install(monkeypatch)
    client = PmPlanningClient(timeout_seconds=240)
    client.process = runtime.process
    client.reader_task = asyncio.create_task(client._read_stdout())
    request = asyncio.create_task(client.request("initialize", {}, timeout=240))
    terminal = asyncio.create_task(client._wait_turn("thread", "turn", deadline=asyncio.get_running_loop().time() + 240))
    try:
        await runtime.entered.wait()
        if eof:
            runtime.stdout.feed_eof()
        else:
            runtime.stdout.set_exception(ConnectionResetError("memory disconnect"))
        await asyncio.gather(client.reader_task, return_exceptions=True)
        results = await asyncio.wait_for(asyncio.gather(request, terminal, return_exceptions=True), 1)
        assert all(isinstance(result, PmError) and result.code == "planning_failed" for result in results)
        assert not client.pending
    finally:
        request.cancel()
        terminal.cancel()
        await asyncio.gather(request, terminal, return_exceptions=True)
        await client.stop()
        await runtime.close()


@pytest.mark.asyncio
async def test_cancel_deadline_retains_startup_without_process_until_retry(tmp_path, monkeypatch):
    runtime = MemoryRuntime("admission")
    runtime.install(monkeypatch)
    auth = tmp_path / "dummy-auth"
    auth.write_text("{}")
    client = PmPlanningClient(auth_file=auth, timeout_seconds=0.02)
    request = asyncio.create_task(client.propose(tmp_path, "snapshot", "review"))
    try:
        await runtime.entered.wait()
        with pytest.raises(PmError) as error:
            await client.cancel_proposal()
        assert error.value.code == "planning_cleanup_failed"
        assert client.process is None and client.cleanup_uncertain
        with pytest.raises(PmError) as refused_stop:
            await client.stop()
        assert refused_stop.value.code == "planning_cleanup_failed"
        assert client.cleanup_uncertain, "process=None must not release a live startup owner"
        runtime.release.set()
        with pytest.raises(PmError) as cancelled:
            await request
        assert cancelled.value.code == "operation_cancelled"
        await client.cancel_proposal()
        assert not client.cleanup_uncertain and runtime.spawns == 0
    finally:
        runtime.release.set()
        await asyncio.gather(request, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
async def test_normal_stop_allows_second_proposal_with_fresh_runtime_output(tmp_path, monkeypatch):
    runtime = MemoryRuntime("none")
    runtime.install(monkeypatch)
    auth = tmp_path / "dummy-auth"
    auth.write_text("{}")
    client = PmPlanningClient(auth_file=auth, timeout_seconds=240)
    try:
        first = await client.propose(tmp_path, "snapshot", "review")
        assert first.proposal_patch == "safe patch"
        runtime.patch = "second safe patch"
        second = await client.propose(tmp_path, "snapshot", "correct the first proposal")
        assert second.proposal_patch == "second safe patch"
        assert runtime.spawns == 2 and runtime.methods.count("turn/start") == 2
        assert not client.cleanup_uncertain
    finally:
        await client.stop()
        await runtime.close()


@pytest.mark.asyncio
async def test_legacy_interrupt_keeps_shared_terminal_protocol():
    from tests.test_pm_planning import FakePlanningClient

    client = FakePlanningClient(response="", status="interrupted")
    await client.cancel("thread-1", "turn-1")
    assert client.requests == [("turn/interrupt", {"threadId": "thread-1", "turnId": "turn-1"})]
    assert client.stop_calls == 1
