"""Execution Integrity 1.0 lease regression tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Event

import pytest

from ollama_workstation.agents.execution_lease import (
    ExecutionLease,
    LeaseUnavailable,
)
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
        def acquire_run_lease(self, run_id: str, owner_id: str, ttl_seconds: int) -> bool:
            return repository.acquire_run_lease(run_id, owner_id, ttl_seconds)

        def renew_run_lease(self, run_id: str, owner_id: str, ttl_seconds: int) -> bool:
            result = repository.renew_run_lease(run_id, owner_id, ttl_seconds)
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
