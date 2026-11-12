"""Execution Integrity 1.0 lease regression tests."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, Thread

import pytest

from ollama_workstation.agents.execution_lease import (
    ExecutionLease,
    LeaseUnavailable,
    WorkspaceAdmission,
)
from ollama_workstation.agents.process_identity import current_process_identity, local_host
from ollama_workstation.agents.recovery import RecoveryStatus, load_recovery_candidates
from ollama_workstation.agents.runtime import AgentRuntime
from ollama_workstation.agents.tools import WorkspaceTools
from ollama_workstation.models.schemas import ModelProfile
from ollama_workstation.storage.repository import Repository

MODEL = "fake:latest"
DIGEST = "sha256:lease-test"


def profile() -> ModelProfile:
    return ModelProfile(
        model_name=MODEL,
        digest=DIGEST,
        generated_at="2026-11-12T00:00:00+00:00",
        professional_title="Repository Cartographer",
        agentability_mode="native_tools",
    )


def assistant(content: str = "", tool_calls: list[dict[str, object]] | None = None) -> dict[str, object]:
    message: dict[str, object] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"message": message}


class ImmediateClient:
    def __init__(self) -> None:
        self.calls = 0

    def chat(self, model: str, messages: list[dict[str, object]], **kwargs: object) -> object:
        self.calls += 1
        return assistant("Lease test complete.")


class CountingTools(WorkspaceTools):
    def __init__(self, workspace: Path) -> None:
        super().__init__(workspace)
        self.invocations: list[str] = []

    def invoke(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        self.invocations.append(name)
        return super().invoke(name, arguments)


def save_checkpoint(repository: Repository, workspace: Path, run_id: str = "lease-run") -> None:
    repository.save_run_checkpoint(
        run_id=run_id,
        model_name=MODEL,
        model_digest=DIGEST,
        agent_title="Repository Cartographer",
        workspace=str(workspace.resolve()),
        harness_mode="native_tools",
        tool_manifest_sha256="manifest",
        state="ready",
        step=0,
        tool_calls=0,
        payload={
            "protocol": 2,
            "task": "Inspect the workspace",
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "user"},
            ],
            "pending_operations": [],
            "step_budget": 12,
            "resume_count": 0,
        },
        created_at="2026-11-12T00:00:00+00:00",
        updated_at="2026-11-12T00:00:00+00:00",
    )


def test_runtime_acquires_and_releases_lease_for_a_new_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = Repository(tmp_path / "state.sqlite3")

    class InspectingClient(ImmediateClient):
        def __init__(self) -> None:
            super().__init__()
            self.leases: list[tuple[str, str]] = []

        def chat(self, model: str, messages: list[dict[str, object]], **kwargs: object) -> object:
            with sqlite3.connect(repository.database) as connection:
                self.leases = connection.execute(
                    "SELECT run_id, owner_id FROM agent_run_leases"
                ).fetchall()
            return super().chat(model, messages, **kwargs)

    client = InspectingClient()
    result = AgentRuntime(client, repository=repository).run(
        MODEL, profile(), "Inspect", CountingTools(workspace)
    )

    assert result.status == "completed"
    assert client.calls == 1
    assert len(client.leases) == 1
    assert client.leases[0][0] == result.run_id
    assert repository.get_run_lease(result.run_id) is None


def test_live_lease_blocks_a_second_owner_and_recovery_is_not_resumable(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = Repository(tmp_path / "state.sqlite3")
    save_checkpoint(repository, workspace)

    with ExecutionLease(repository, "lease-run", "owner-a", ttl_seconds=60):
        candidate = load_recovery_candidates(repository)[0]
        assert candidate.status is RecoveryStatus.LEASED
        assert candidate.lease_active is True
        assert candidate.resumable is False
        with pytest.raises(LeaseUnavailable):
            with ExecutionLease(repository, "lease-run", "owner-b", ttl_seconds=60):
                pass

        client = ImmediateClient()
        checkpoint = repository.get_run_checkpoint("lease-run")
        assert checkpoint is not None
        with pytest.raises(LeaseUnavailable):
            AgentRuntime(client, repository=repository).run(
                MODEL, profile(), "Inspect", CountingTools(workspace),
                resume_checkpoint=checkpoint,
            )
        assert client.calls == 0

    candidate = load_recovery_candidates(repository)[0]
    assert candidate.status is RecoveryStatus.RESUMABLE
    assert candidate.lease_active is False


def test_lease_loss_stops_before_tool_execution(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = Repository(tmp_path / "state.sqlite3")

    class HijackingClient(ImmediateClient):
        def chat(self, model: str, messages: list[dict[str, object]], **kwargs: object) -> object:
            with sqlite3.connect(repository.database) as connection:
                row = connection.execute(
                    "SELECT run_id, owner_id FROM agent_run_leases"
                ).fetchone()
            assert row is not None
            run_id, owner = str(row[0]), str(row[1])
            assert repository.release_run_lease(run_id, owner)
            assert repository.acquire_run_lease(run_id, "intruder", ttl_seconds=60)
            return assistant(tool_calls=[
                {"function": {"name": "write_file", "arguments": {
                    "path": "must-not-exist.txt", "content": "lease lost",
                }}},
            ])

    tools = CountingTools(workspace)
    result = AgentRuntime(HijackingClient(), repository=repository).run(
        MODEL, profile(), "Inspect", tools
    )

    assert result.status == "lease_lost"
    assert tools.invocations == []
    assert not (workspace / "must-not-exist.txt").exists()
    repository.release_run_lease(result.run_id, "intruder")


def test_lease_heartbeat_renews_ownership(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "state.sqlite3")
    renewed = Event()

    class RenewalSpy:
        def acquire_run_lease(
            self, run_id: str, owner_id: str, ttl_seconds: int,
            **metadata: object,
        ) -> bool:
            return repository.acquire_run_lease(
                run_id, owner_id, ttl_seconds, **metadata  # type: ignore[arg-type]
            )

        def renew_run_lease(
            self, run_id: str, owner_id: str, ttl_seconds: int,
            **metadata: object,
        ) -> bool:
            result = repository.renew_run_lease(
                run_id, owner_id, ttl_seconds, **metadata  # type: ignore[arg-type]
            )
            renewed.set()
            return result

        def release_run_lease(self, run_id: str, owner_id: str) -> bool:
            return repository.release_run_lease(run_id, owner_id)

    with ExecutionLease(
        RenewalSpy(), "lease-run", "owner-a", ttl_seconds=60, heartbeat_seconds=1
    ):
        assert renewed.wait(2.0)
    assert repository.get_run_lease("lease-run") is None


def test_lease_context_releases_on_exception(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "state.sqlite3")
    with pytest.raises(RuntimeError, match="boom"):
        with ExecutionLease(repository, "lease-run", "owner-a", ttl_seconds=60):
            raise RuntimeError("boom")
    assert repository.get_run_lease("lease-run") is None


class BlockingClient(ImmediateClient):
    def __init__(self, started: Event, release: Event) -> None:
        super().__init__()
        self.started = started
        self.release = release

    def chat(self, model: str, messages: list[dict[str, object]], **kwargs: object) -> object:
        self.calls += 1
        self.started.set()
        assert self.release.wait(5.0)
        return assistant("First run complete.")


def test_second_runtime_against_a_held_workspace_is_refused_without_mutation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = Repository(tmp_path / "workspace-state.sqlite3")
    started = Event()
    release = Event()
    first_box: dict[str, object] = {}

    def first_run() -> None:
        first_box["result"] = AgentRuntime(
            BlockingClient(started, release), repository=repository
        ).run(MODEL, profile(), "First run", CountingTools(workspace))

    thread = Thread(target=first_run, daemon=True)
    thread.start()
    try:
        assert started.wait(5.0)
        second_client = ImmediateClient()
        second = AgentRuntime(second_client, repository=repository).run(
            MODEL, profile(), "Second run", CountingTools(workspace)
        )
        assert second.status == "workspace_busy"
        assert second_client.calls == 0
        assert second.steps == 0
        assert not (workspace / "must-not-exist.txt").exists()
        assert repository.get_run_lease(second.run_id) is None
        assert repository.get_run_checkpoint(second.run_id) is None
        assert any(item.kind == "workspace_busy" for item in second.trace)
    finally:
        release.set()
        thread.join(timeout=5.0)

    assert first_box["result"].status == "completed"  # type: ignore[union-attr]
    assert repository.get_workspace_lease(workspace) is None
    later = AgentRuntime(ImmediateClient(), repository=repository).run(
        MODEL, profile(), "Later run", CountingTools(workspace)
    )
    assert later.status == "completed"


def test_dead_owner_run_lease_is_reclaimed_before_ttl(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "dead-run.sqlite3")
    dead_owner = f"{local_host()}:2147483647:dead:run"
    assert repository.acquire_run_lease(
        "dead-run", dead_owner, ttl_seconds=300,
        owner_host=local_host(), owner_pid=2_147_483_647, owner_start="dead-process",
    )
    row = repository.get_run_lease("dead-run")
    assert row is not None
    assert row["active"] is False
    identity = current_process_identity()
    assert repository.acquire_run_lease(
        "dead-run", "live-owner", ttl_seconds=300, **identity.as_lease_metadata()
    )
    live_row = repository.get_run_lease("dead-run")
    assert live_row is not None
    assert live_row["owner_id"] == "live-owner"
    assert live_row["active"] is True
    assert repository.release_run_lease("dead-run", "live-owner")


def test_live_owner_run_lease_is_never_reclaimed(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "live-run.sqlite3")
    identity = current_process_identity()
    assert repository.acquire_run_lease(
        "live-run", "owner-a", ttl_seconds=300, **identity.as_lease_metadata()
    )
    assert not repository.acquire_run_lease(
        "live-run", "owner-b", ttl_seconds=300, **identity.as_lease_metadata()
    )
    assert repository.get_run_lease("live-run")["active"] is True  # type: ignore[index]


def test_dead_owner_workspace_lease_is_reclaimed_before_ttl(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = Repository(tmp_path / "dead-workspace.sqlite3")
    assert repository.acquire_workspace_lease(
        workspace, "dead-run", f"{local_host()}:2147483647:dead", ttl_seconds=300,
        owner_host=local_host(), owner_pid=2_147_483_647, owner_start="dead-process",
    )
    assert repository.get_workspace_lease(workspace)["active"] is False  # type: ignore[index]
    identity = current_process_identity()
    assert repository.acquire_workspace_lease(
        workspace, "live-run", "live-owner", ttl_seconds=300,
        **identity.as_lease_metadata(),
    )
    row = repository.get_workspace_lease(workspace)
    assert row is not None
    assert row["run_id"] == "live-run"
    assert row["active"] is True
    assert repository.release_workspace_lease(workspace, "live-owner")


def test_live_owner_workspace_lease_is_never_reclaimed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = Repository(tmp_path / "live-workspace.sqlite3")
    identity = current_process_identity()
    assert repository.acquire_workspace_lease(
        workspace, "winner", "owner-a", ttl_seconds=300,
        **identity.as_lease_metadata(),
    )
    assert not repository.acquire_workspace_lease(
        workspace, "loser", "owner-b", ttl_seconds=300,
        **identity.as_lease_metadata(),
    )
    assert repository.get_workspace_lease(workspace)["owner_id"] == "owner-a"  # type: ignore[index]


def test_concurrent_workspace_reclamation_has_exactly_one_owner(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = Repository(tmp_path / "race.sqlite3")
    assert repository.acquire_workspace_lease(
        workspace, "dead-run", f"{local_host()}:2147483647:dead", ttl_seconds=300,
        owner_host=local_host(), owner_pid=2_147_483_647, owner_start="dead-process",
    )
    barrier = Barrier(8)
    identity = current_process_identity()

    def attempt(index: int) -> bool:
        barrier.wait(timeout=5.0)
        return repository.acquire_workspace_lease(
            workspace, f"run-{index}", f"owner-{index}", ttl_seconds=300,
            **identity.as_lease_metadata(),
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert results.count(True) == 1
    row = repository.get_workspace_lease(workspace)
    assert row is not None
    assert row["owner_id"].startswith("owner-")
    assert row["active"] is True


def test_concurrent_run_reclamation_has_exactly_one_owner(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "run-race.sqlite3")
    assert repository.acquire_run_lease(
        "dead-run", f"{local_host()}:2147483647:dead", ttl_seconds=300,
        owner_host=local_host(), owner_pid=2_147_483_647, owner_start="dead-process",
    )
    barrier = Barrier(8)
    identity = current_process_identity()

    def attempt(index: int) -> bool:
        barrier.wait(timeout=5.0)
        return repository.acquire_run_lease(
            "dead-run", f"owner-{index}", ttl_seconds=300,
            **identity.as_lease_metadata(),
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert results.count(True) == 1
    row = repository.get_run_lease("dead-run")
    assert row is not None
    assert row["owner_id"].startswith("owner-")
    assert row["active"] is True


def test_runtime_resume_is_refused_by_a_foreign_workspace_admission(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = Repository(tmp_path / "resume-workspace.sqlite3")
    save_checkpoint(repository, workspace)
    checkpoint = repository.get_run_checkpoint("lease-run")
    assert checkpoint is not None

    with WorkspaceAdmission(
        repository, workspace, "holder-run", "holder-owner", ttl_seconds=60
    ):
        candidate = load_recovery_candidates(repository)[0]
        assert candidate.status is RecoveryStatus.WORKSPACE_BUSY
        assert candidate.workspace_active is True
        assert candidate.workspace_run_id == "holder-run"
        assert candidate.resumable is False

        client = ImmediateClient()
        result = AgentRuntime(client, repository=repository).run(
            MODEL, profile(), "Inspect the workspace", CountingTools(workspace),
            resume_checkpoint=checkpoint,
        )
        assert result.status == "workspace_busy"
        assert client.calls == 0
        assert repository.get_run_lease("lease-run") is None

    candidate = load_recovery_candidates(repository)[0]
    assert candidate.status is RecoveryStatus.RESUMABLE
    assert candidate.workspace_active is False
