"""Admission queue, automatic unload and parallel workforce waves."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from ollama_workstation.agentification.resources import (
    GIB,
    MIB,
    AdmissionController,
    ResourceDenied,
    ResourceScheduler,
)
from ollama_workstation.agents.orchestration import (
    AgentAssignment,
    TaskSpec,
    WorkforceOrchestrator,
    WorkforcePlan,
)
from ollama_workstation.models.schemas import ModelProfile

# Weights in GiB. With no geometry the KV estimate is the 1 GiB fallback at 4096
# tokens, so a 6 GiB model needs 7.6 GiB and a 3 GiB model 4.3 GiB.
SIZES = {"big-a": 6, "big-b": 6, "small": 3, "small-2": 3}
VRAM_TOTAL = 12


class FakeOllama:
    """Resident set and free VRAM move together, like a real GPU."""

    def __init__(self) -> None:
        self.resident: dict[str, int] = {}
        self.unloaded: list[str] = []
        self.lock = threading.Lock()

    def list_models(self) -> list[dict[str, object]]:
        return [{"name": name, "size": gib * GIB} for name, gib in SIZES.items()]

    def show_model(self, _name: str) -> dict[str, object]:
        return {}

    def running_models(self) -> list[dict[str, object]]:
        with self.lock:
            return [{"name": name, "size": size, "size_vram": size}
                    for name, size in self.resident.items()]

    def load(self, name: str) -> None:
        with self.lock:
            self.resident[name] = int(SIZES[name] * GIB * 1.1 + GIB)

    def unload(self, name: str) -> None:
        with self.lock:
            self.resident.pop(name, None)
            self.unloaded.append(name)

    def snapshot(self) -> dict[str, object]:
        with self.lock:
            used = sum(self.resident.values())
        free_mib = int((VRAM_TOTAL * GIB - used) / MIB)
        return {"system_memory": {"total_bytes": 8 * GIB, "available_bytes": 2 * GIB},
                "nvidia_gpus": [{"name": "gpu", "memory_total_mib": VRAM_TOTAL * 1024,
                                 "memory_free_mib": free_mib}]}


def scheduler(fake: FakeOllama, **kwargs: object) -> ResourceScheduler:
    return ResourceScheduler(AdmissionController(fake, snapshot=fake.snapshot),
                             poll_seconds=0.05, **kwargs)  # type: ignore[arg-type]


def test_idle_resident_model_is_unloaded_automatically() -> None:
    fake = FakeOllama()
    fake.load("big-a")  # resident, but no agent holds it
    with scheduler(fake).lease("big-b") as decision:
        assert decision.admitted
    assert fake.unloaded == ["big-a"]


def test_load_waits_for_a_leased_model_then_proceeds() -> None:
    fake = FakeOllama()
    queue = scheduler(fake)
    order: list[str] = []
    holding = threading.Event()

    def first() -> None:
        with queue.lease("big-a"):
            fake.load("big-a")
            holding.set()
            time.sleep(0.4)
            order.append("a-released")

    thread = threading.Thread(target=first)
    thread.start()
    holding.wait(5)
    with queue.lease("big-b", timeout=10):
        order.append("b-admitted")
    thread.join()
    assert order == ["a-released", "b-admitted"]
    assert fake.unloaded == ["big-a"]  # unloaded only once it became idle


def test_reserved_but_not_yet_resident_load_is_counted() -> None:
    fake = FakeOllama()
    queue = scheduler(fake)
    with queue.lease("big-a"):  # not resident yet: Ollama has not loaded it
        with pytest.raises(ResourceDenied, match="waited"):
            queue.acquire("big-b", timeout=0.2)
    # Without the reservation the second load would have been admitted at once.
    assert AdmissionController(fake, snapshot=fake.snapshot).evaluate("big-b").outcome == "admit"


def test_two_small_models_run_together() -> None:
    fake = FakeOllama()
    queue = scheduler(fake)
    with queue.lease("small"):
        fake.load("small")
        with queue.lease("small-2", timeout=0.2) as decision:
            assert decision.admitted
    assert fake.unloaded == []


def test_same_model_leases_share_and_release_cleanly() -> None:
    fake = FakeOllama()
    queue = scheduler(fake)
    with queue.lease("small"), queue.lease("small"):
        assert queue.active() == {"small": 2}
    assert queue.active() == {}


def test_gate_require_inside_a_lease_does_not_re_admit() -> None:
    fake = FakeOllama()
    queue = scheduler(fake)
    with queue.lease("big-a"):
        assert queue.require("big-a").reasons == ("reserved by the resource scheduler",)


def test_model_that_never_fits_is_refused_immediately() -> None:
    fake = FakeOllama()
    SIZES["huge"] = 40
    try:
        started = time.monotonic()
        with pytest.raises(ResourceDenied):
            scheduler(fake).acquire("huge", timeout=30)
        assert time.monotonic() - started < 5
    finally:
        SIZES.pop("huge")


class ConcurrencyClient:
    """Answers every chat after a pause and records how many ran at once."""

    grounded = True

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.lock = threading.Lock()
        self.started: list[str] = []

    def chat(self, model: str, messages: list[dict[str, object]], **_kwargs: object) -> dict[str, object]:
        task = next((str(m["content"]) for m in messages if m.get("role") == "user"), "")
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.started.append(task)
        time.sleep(0.3)
        with self.lock:
            self.active -= 1
        observed = any(m.get("role") == "tool" for m in messages)
        if self.grounded and "SURVEY" in task and not observed:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "list_files", "arguments": {}}}]}}
        return {"message": {"role": "assistant", "content": "Done."}}


def _plan(max_parallel: int) -> WorkforcePlan:
    return WorkforcePlan(
        objective="Survey then write.",
        tasks=(
            TaskSpec("survey-1", "researcher", "SURVEY ONE", read_only=True),
            TaskSpec("survey-2", "researcher", "SURVEY TWO", read_only=True),
            TaskSpec("survey-3", "researcher", "SURVEY THREE", read_only=True),
            TaskSpec("write", "writer", "WRITE REPORT", depends_on=("survey-1", "survey-2", "survey-3")),
        ),
        require_approval=False, max_parallel=max_parallel,
    )


def _assignments() -> dict[str, AgentAssignment]:
    profile = ModelProfile(model_name="local:latest", digest="sha256:local", generated_at="now",
                           agentability_mode="native_tools")
    return {task_id: AgentAssignment(task_id, "local:latest", profile, task_id != "write")
            for task_id in ("survey-1", "survey-2", "survey-3", "write")}


def test_read_only_tasks_run_in_parallel_and_the_writer_runs_alone(tmp_path: Path) -> None:
    client = ConcurrencyClient()
    result = WorkforceOrchestrator(client, None).run(
        _plan(max_parallel=2), tmp_path / "ws", _assignments(), approval=lambda _m: True)
    assert result.status == "completed", result.final_message
    assert client.peak == 2  # capped at max_parallel, though three were ready
    assert any("WRITE REPORT" in item for item in client.started[-1:])


def test_ungrounded_research_fails_and_blocks_dependants(tmp_path: Path) -> None:
    """Live finding: a reviewer that never read its file answered "does not exist",
    was counted complete, and the writer invented the missing figures."""
    client = ConcurrencyClient()
    client.grounded = False
    result = WorkforceOrchestrator(client, None).run(
        _plan(max_parallel=3), tmp_path / "ws", _assignments(), approval=lambda _m: True)
    statuses = {task.task_id: task.status for task in result.tasks}
    assert statuses["survey-1"] == "verification_failed"
    assert statuses["write"] == "blocked"
    assert not any("WRITE REPORT" in item for item in client.started)
    detail = result.tasks[0].verification[-1]
    assert detail["kind"] == "grounded_in_workspace" and detail["passed"] is False


def test_max_parallel_one_keeps_the_serial_behaviour(tmp_path: Path) -> None:
    client = ConcurrencyClient()
    result = WorkforceOrchestrator(client, None).run(
        _plan(max_parallel=1), tmp_path / "ws", _assignments(), approval=lambda _m: True)
    assert result.status == "completed"
    assert client.peak == 1


def test_children_hold_scheduler_leases_while_they_run(tmp_path: Path) -> None:
    fake = FakeOllama()
    queue = scheduler(fake)
    seen: list[dict[str, int]] = []

    class LeaseWatchingClient(ConcurrencyClient):
        def chat(self, model: str, messages: list[dict[str, object]], **kwargs: object) -> dict[str, object]:
            seen.append(queue.active())
            return super().chat(model, messages, **kwargs)

    SIZES["local:latest"] = 3
    try:
        result = WorkforceOrchestrator(LeaseWatchingClient(), None, resource_scheduler=queue).run(
            _plan(max_parallel=3), tmp_path / "ws", _assignments(), approval=lambda _m: True)
    finally:
        SIZES.pop("local:latest")
    assert result.status == "completed"
    assert seen and all(item.get("local:latest", 0) >= 1 for item in seen)
    assert queue.active() == {}
