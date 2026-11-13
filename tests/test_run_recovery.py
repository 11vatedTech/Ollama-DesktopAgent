"""End-to-end durable execution recovery tests.

These tests exercise the real runtime, repository and recovery policy. No
provider is involved: scripted clients return deterministic Ollama-shaped
responses, and process death is simulated with ``SystemExit``, which the runtime
must not intercept.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import pytest

import ollama_workstation.cli as cli_module
from ollama_workstation.agents.execution_lease import WorkspaceAdmission
from ollama_workstation.agents.recovery import (
    CHECKPOINT_PROTOCOL,
    CheckpointIntegrityError,
    CheckpointNotResumable,
    RecoveryStatus,
    load_recovery_candidates,
    verify_resume_checkpoint,
)
from ollama_workstation.agents.runtime import AgentRuntime
from ollama_workstation.agents.tools import WorkspaceTools
from ollama_workstation.config import Settings
from ollama_workstation.models.schemas import ModelProfile, ModelRef
from ollama_workstation.storage.repository import Repository

MODEL = "fake:latest"
DIGEST = "sha256:abc"


def make_profile(**overrides: object) -> ModelProfile:
    values: dict[str, object] = {
        "model_name": MODEL,
        "digest": DIGEST,
        "generated_at": "2026-11-10T00:00:00+00:00",
        "professional_title": "Repository Cartographer",
        "agentability_mode": "native_tools",
    }
    values.update(overrides)
    return ModelProfile(**values)  # type: ignore[arg-type]


class ScriptedClient:
    """Provider stub that raises exceptions placed in the script."""

    def __init__(self, script: list[object]) -> None:
        self.script = list(script)
        self.calls = 0

    def chat(self, model: str, messages: list[dict[str, object]], **kwargs: object) -> object:
        self.calls += 1
        if not self.script:
            raise AssertionError("unexpected provider call")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def assistant(content: str = "", tool_calls: list[dict[str, object]] | None = None) -> dict[str, object]:
    message: dict[str, object] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"message": message}


def tool_call(name: str, **arguments: object) -> dict[str, object]:
    return {"function": {"name": name, "arguments": arguments}}


class CountingTools(WorkspaceTools):
    def __init__(self, workspace: Path, **kwargs: object) -> None:
        super().__init__(workspace, **kwargs)  # type: ignore[arg-type]
        self.invocations: list[tuple[str, dict[str, object]]] = []

    def invoke(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        self.invocations.append((name, dict(arguments)))
        return super().invoke(name, arguments)


class DyingTools(CountingTools):
    """Simulate abrupt process death after a tool's side effect was requested."""

    def invoke(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        self.invocations.append((name, dict(arguments)))
        raise SystemExit("simulated process death during a tool operation")


def tool_manifest(tools: WorkspaceTools) -> str:
    specs = sorted(
        tools.definitions(),
        key=lambda entry: str(entry.get("function", {}).get("name", "")),
    )
    encoded = json.dumps(specs, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def save_checkpoint(
    repository: Repository,
    *,
    workspace: Path,
    manifest: str,
    profile: ModelProfile | None = None,
    task: str = "Inspect the workspace",
    state: str = "ready",
    step: int = 1,
    step_budget: int = 12,
    updated_at: str | None = None,
    run_id: str = "run-checkpoint",
) -> None:
    selected = profile or make_profile()
    timestamp = updated_at or datetime.now(UTC).isoformat()
    repository.save_run_checkpoint(
        run_id=run_id,
        model_name=selected.model_name,
        model_digest=selected.digest,
        agent_title=selected.professional_title,
        workspace=str(workspace.resolve()),
        harness_mode=selected.agentability_mode,
        tool_manifest_sha256=manifest,
        state=state,
        step=step,
        tool_calls=0,
        payload={
            "protocol": CHECKPOINT_PROTOCOL,
            "task": task,
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "user"},
            ],
            "pending_operations": [],
            "step_budget": step_budget,
            "resume_count": 0,
        },
        created_at=timestamp,
        updated_at=timestamp,
    )


def binding_for(workspace: Path, profile: ModelProfile, manifest: str) -> dict[str, str]:
    return {
        "model_name": profile.model_name,
        "model_digest": profile.digest,
        "agent_title": profile.professional_title,
        "workspace": str(workspace.resolve()),
        "harness_mode": profile.agentability_mode,
        "tool_manifest_sha256": manifest,
    }


def test_completed_run_discards_checkpoint(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = Repository(tmp_path / "state.sqlite3")
    client = ScriptedClient([assistant("Done.")])
    result = AgentRuntime(client, repository=repository).run(
        MODEL, make_profile(), "Say done", CountingTools(workspace)
    )
    assert result.status == "completed"
    assert repository.get_run_checkpoint(result.run_id) is None
    assert load_recovery_candidates(repository) == []


def test_crash_before_provider_request_resumes_without_replaying_tools(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("hello", encoding="utf-8")
    repository = Repository(tmp_path / "state.sqlite3")
    tools = CountingTools(workspace)
    client = ScriptedClient([
        assistant(tool_calls=[tool_call("read_file", path="hello.txt")]),
        SystemExit("simulated process death before the next request"),
    ])
    with pytest.raises(SystemExit):
        AgentRuntime(client, repository=repository).run(
            MODEL, make_profile(), "Read hello.txt", tools
        )
    assert len(tools.invocations) == 1

    [metadata] = repository.list_run_checkpoints()
    run_id = str(metadata["run_id"])
    checkpoint = repository.get_run_checkpoint(run_id)
    assert checkpoint is not None
    assert checkpoint["state"] == "awaiting_model"
    assert checkpoint["step"] == 1
    assert checkpoint["payload"]["protocol"] == CHECKPOINT_PROTOCOL

    candidates = load_recovery_candidates(repository)
    assert [(item.run_id, item.status) for item in candidates] == [
        (run_id, RecoveryStatus.RESUMABLE)
    ]

    resumed_tools = CountingTools(workspace)
    resumed_client = ScriptedClient([assistant("Resumed report.")])
    result = AgentRuntime(resumed_client, repository=repository).run(
        MODEL, make_profile(), "Read hello.txt", resumed_tools,
        resume_checkpoint=checkpoint,
    )
    assert result.status == "completed"
    assert result.run_id == run_id
    assert resumed_tools.invocations == []
    resumed_event = next(event for event in result.trace if event.kind == "resumed")
    assert resumed_event.data["resume_count"] == 1
    assert repository.get_run_checkpoint(run_id) is None


def test_interrupted_tool_requires_review_and_is_never_replayed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = Repository(tmp_path / "state.sqlite3")
    tools = DyingTools(workspace)
    client = ScriptedClient([
        assistant(tool_calls=[
            tool_call("write_file", path="new.txt", content="must not be replayed")
        ]),
    ])
    with pytest.raises(SystemExit):
        AgentRuntime(client, repository=repository).run(
            MODEL, make_profile(), "Write a file", tools
        )
    [metadata] = repository.list_run_checkpoints()
    run_id = str(metadata["run_id"])
    interrupted = repository.get_run_checkpoint(run_id)
    assert interrupted is not None
    assert interrupted["state"] == "tool_in_progress"
    assert interrupted["payload"]["pending_operations"][0]["name"] == "write_file"
    assert "content" not in interrupted["payload"]["pending_operations"][0]

    candidates = load_recovery_candidates(repository)
    assert candidates[0].status is RecoveryStatus.NEEDS_REVIEW
    assert "human review" in candidates[0].reason
    reconciled = repository.get_run_checkpoint(run_id)
    assert reconciled is not None
    assert reconciled["state"] == "needs_review"

    replay_client = ScriptedClient([])
    with pytest.raises(CheckpointNotResumable):
        AgentRuntime(replay_client, repository=repository).run(
            MODEL, make_profile(), "Write a file", CountingTools(workspace),
            resume_checkpoint=reconciled,
        )
    assert tools.invocations == [
        ("write_file", {"path": "new.txt", "content": "must not be replayed"})
    ]
    assert replay_client.calls == 0


def test_resume_rejects_every_binding_change(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    other_workspace = tmp_path / "other"
    other_workspace.mkdir()
    repository = Repository(tmp_path / "state.sqlite3")
    profile = make_profile()
    base_manifest = tool_manifest(WorkspaceTools(workspace))
    save_checkpoint(repository, workspace=workspace, manifest=base_manifest, profile=profile)
    checkpoint = repository.get_run_checkpoint("run-checkpoint")
    assert checkpoint is not None

    scenarios: list[tuple[str, dict[str, object]]] = [
        ("model_name", {"model": "other:latest"}),
        ("model_digest", {"profile": make_profile(digest="sha256:other")}),
        ("agent_title", {"profile": make_profile(professional_title="Different")}),
        ("harness_mode", {"profile": make_profile(agentability_mode="guided")}),
        ("workspace", {"tools": CountingTools(other_workspace)}),
        ("task", {"task": "A different task"}),
        ("tool_manifest_sha256", {"tools": CountingTools(workspace, allow_shell=True)}),
    ]
    for field, overrides in scenarios:
        client = ScriptedClient([])
        arguments: dict[str, object] = {
            "model": MODEL,
            "profile": make_profile(),
            "task": "Inspect the workspace",
            "tools": CountingTools(workspace),
            "resume_checkpoint": checkpoint,
        }
        arguments.update(overrides)
        with pytest.raises(CheckpointNotResumable) as rejected:
            AgentRuntime(client, repository=repository).run(**arguments)  # type: ignore[arg-type]
        assert field in str(rejected.value)
        assert client.calls == 0


def test_tampered_binding_column_fails_integrity_check(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = Repository(tmp_path / "state.sqlite3")
    save_checkpoint(
        repository, workspace=workspace, manifest=tool_manifest(WorkspaceTools(workspace))
    )
    with sqlite3.connect(repository.database) as connection:
        connection.execute(
            "UPDATE agent_run_checkpoints SET workspace=? WHERE run_id=?",
            (str(tmp_path / "evil"), "run-checkpoint"),
        )
    with pytest.raises(ValueError, match="binding integrity"):
        repository.get_run_checkpoint("run-checkpoint")


def test_tampered_freshness_column_fails_integrity_check(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = Repository(tmp_path / "state.sqlite3")
    save_checkpoint(
        repository, workspace=workspace, manifest=tool_manifest(WorkspaceTools(workspace))
    )
    with sqlite3.connect(repository.database) as connection:
        connection.execute(
            "UPDATE agent_run_checkpoints SET updated_at=? WHERE run_id=?",
            (datetime.now(UTC).isoformat(), "run-checkpoint"),
        )
    with pytest.raises(ValueError, match="binding integrity"):
        repository.get_run_checkpoint("run-checkpoint")


def test_corrupt_payload_fails_integrity_check(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = Repository(tmp_path / "state.sqlite3")
    save_checkpoint(
        repository, workspace=workspace, manifest=tool_manifest(WorkspaceTools(workspace))
    )
    with sqlite3.connect(repository.database) as connection:
        connection.execute(
            "UPDATE agent_run_checkpoints SET payload_json=? WHERE run_id=?",
            ("{", "run-checkpoint"),
        )
    with pytest.raises(ValueError, match="payload integrity"):
        repository.get_run_checkpoint("run-checkpoint")


def test_stale_checkpoint_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = Repository(tmp_path / "state.sqlite3")
    profile = make_profile()
    manifest = tool_manifest(WorkspaceTools(workspace))
    old = (datetime.now(UTC) - timedelta(hours=100)).isoformat()
    save_checkpoint(
        repository, workspace=workspace, manifest=manifest, profile=profile, updated_at=old
    )
    checkpoint = repository.get_run_checkpoint("run-checkpoint")
    assert checkpoint is not None
    with pytest.raises(CheckpointNotResumable, match="stale"):
        verify_resume_checkpoint(
            checkpoint,
            binding=binding_for(workspace, profile, manifest),
            task="Inspect the workspace",
        )
    assert load_recovery_candidates(repository)[0].status is RecoveryStatus.STALE


def test_completed_run_checkpoint_is_superseded(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = Repository(tmp_path / "state.sqlite3")
    manifest = tool_manifest(WorkspaceTools(workspace))
    save_checkpoint(repository, workspace=workspace, manifest=manifest)
    repository.save_run(
        "run-checkpoint", MODEL, "Repository Cartographer", "Inspect the workspace",
        "completed", "2026-11-10T00:00:00+00:00", "2026-11-10T00:00:01+00:00", [],
    )
    assert load_recovery_candidates(repository)[0].status is RecoveryStatus.SUPERSEDED
    checkpoint = repository.get_run_checkpoint("run-checkpoint")
    assert checkpoint is not None
    with pytest.raises(CheckpointNotResumable, match="completed"):
        verify_resume_checkpoint(
            checkpoint,
            binding=binding_for(workspace, make_profile(), manifest),
            task="Inspect the workspace",
        )


def test_exhausted_step_budget_is_not_resumable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = Repository(tmp_path / "state.sqlite3")
    profile = make_profile()
    manifest = tool_manifest(WorkspaceTools(workspace))
    save_checkpoint(
        repository, workspace=workspace, manifest=manifest, profile=profile,
        step=3, step_budget=3,
    )
    assert load_recovery_candidates(repository)[0].status is RecoveryStatus.EXHAUSTED
    checkpoint = repository.get_run_checkpoint("run-checkpoint")
    assert checkpoint is not None
    with pytest.raises(CheckpointNotResumable, match="budget"):
        verify_resume_checkpoint(
            checkpoint,
            binding=binding_for(workspace, profile, manifest),
            task="Inspect the workspace",
        )


def test_legacy_checkpoint_protocol_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    checkpoint = {
        "run_id": "legacy",
        "model_name": MODEL,
        "model_digest": DIGEST,
        "agent_title": "Repository Cartographer",
        "workspace": str(workspace.resolve()),
        "harness_mode": "native_tools",
        "tool_manifest_sha256": "manifest",
        "state": "ready",
        "step": 0,
        "tool_calls": 0,
        "updated_at": datetime.now(UTC).isoformat(),
        "payload": {
            "protocol": 1,
            "task": "Inspect the workspace",
            "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
            "step_budget": 12,
        },
    }
    with pytest.raises(CheckpointIntegrityError, match="protocol"):
        verify_resume_checkpoint(
            checkpoint,
            binding=binding_for(workspace, make_profile(), "manifest"),
            task="Inspect the workspace",
        )


def test_cancelled_run_retains_a_resumable_checkpoint(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("hello", encoding="utf-8")
    repository = Repository(tmp_path / "state.sqlite3")
    cancel_event = Event()

    class CancellingTools(CountingTools):
        def invoke(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
            result = super().invoke(name, arguments)
            cancel_event.set()
            return result

    client = ScriptedClient([
        assistant(tool_calls=[tool_call("read_file", path="hello.txt")]),
    ])
    result = AgentRuntime(client, repository=repository).run(
        MODEL, make_profile(), "Read hello.txt", CancellingTools(workspace),
        cancel_event=cancel_event,
    )
    assert result.status == "cancelled"
    checkpoint = repository.get_run_checkpoint(result.run_id)
    assert checkpoint is not None
    assert checkpoint["state"] == "ready"
    assert load_recovery_candidates(repository)[0].status is RecoveryStatus.RESUMABLE

    resumed = AgentRuntime(
        ScriptedClient([assistant("Finished after cancellation.")]), repository=repository
    ).run(
        MODEL, make_profile(), "Read hello.txt", CountingTools(workspace),
        resume_checkpoint=checkpoint,
    )
    assert resumed.status == "completed"
    assert resumed.run_id == result.run_id


def test_cli_lists_and_discards_recovery_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = Repository(tmp_path / "cli.sqlite3")
    save_checkpoint(
        repository, workspace=workspace, manifest=tool_manifest(WorkspaceTools(workspace))
    )
    monkeypatch.setattr(
        cli_module, "_services",
        lambda: (None, Settings(), ScriptedClient([]), repository),
    )
    assert cli_module.main(["recover", "--list"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["status"] == "resumable"
    assert payload[0]["run_id"] == "run-checkpoint"
    assert cli_module.main(["recover", "run-checkpoint", "--discard"]) == 0
    assert repository.get_run_checkpoint("run-checkpoint") is None


def test_cli_refuses_interrupted_tool_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = Repository(tmp_path / "cli-interrupted.sqlite3")
    save_checkpoint(
        repository, workspace=workspace, manifest=tool_manifest(WorkspaceTools(workspace)),
        state="tool_in_progress",
    )
    monkeypatch.setattr(
        cli_module, "_services",
        lambda: (None, Settings(), ScriptedClient([]), repository),
    )
    assert cli_module.main(["recover", "run-checkpoint"]) == 2
    captured = capsys.readouterr()
    assert "not resumable" in captured.err
    assert "human review" in captured.err
    assert repository.get_run_checkpoint("run-checkpoint")["state"] == "needs_review"


def test_cli_resumes_verified_run_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("hello", encoding="utf-8")
    repository = Repository(tmp_path / "cli-resume.sqlite3")
    profile = make_profile()
    repository.save_profile(profile)
    tools = CountingTools(workspace)
    crashed = ScriptedClient([
        assistant(tool_calls=[tool_call("read_file", path="hello.txt")]),
        SystemExit("simulated crash before the next request"),
    ])
    with pytest.raises(SystemExit):
        AgentRuntime(crashed, repository=repository).run(
            MODEL, profile, "Read hello.txt", tools
        )
    [metadata] = repository.list_run_checkpoints()
    run_id = str(metadata["run_id"])

    fake_client = ScriptedClient([assistant("CLI resume complete.")])

    class StubProfiler:
        def __init__(self, client: object) -> None:
            self.client = client

        def inventory(self) -> list[ModelRef]:
            return [ModelRef(name=MODEL, digest=DIGEST)]

    monkeypatch.setattr(
        cli_module, "_services",
        lambda: (None, Settings(), fake_client, repository),
    )
    monkeypatch.setattr(cli_module, "ModelProfiler", StubProfiler)
    assert cli_module.main(["recover", run_id]) == 0
    captured = capsys.readouterr()
    assert "CLI resume complete." in captured.out
    assert repository.list_runs()[0]["status"] == "completed"
    assert repository.get_run_checkpoint(run_id) is None


def test_cli_recovery_refuses_a_workspace_held_by_another_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = Repository(tmp_path / "cli-workspace.sqlite3")
    save_checkpoint(
        repository, workspace=workspace, manifest=tool_manifest(WorkspaceTools(workspace))
    )
    monkeypatch.setattr(
        cli_module, "_services",
        lambda: (None, Settings(), ScriptedClient([]), repository),
    )
    with WorkspaceAdmission(
        repository, workspace, "holder-run", "holder-owner", ttl_seconds=60
    ):
        assert cli_module.main(["recover", "run-checkpoint"]) == 2
        captured = capsys.readouterr()
        assert "not resumable" in captured.err
        assert "workspace" in captured.err.lower()
    assert repository.get_workspace_lease(workspace) is None


def test_resumed_run_continues_the_same_time_budget(tmp_path: Path, monkeypatch) -> None:
    """A crash must not hand the run a fresh time budget (KNOWN_RISKS R6)."""
    from ollama_workstation.agents.runtime import RunPolicy

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("hello", encoding="utf-8")
    repository = Repository(tmp_path / "state.sqlite3")
    now = {"value": 1000.0}

    def tick() -> float:
        now["value"] += 15.0
        return now["value"]

    monkeypatch.setattr("ollama_workstation.agents.runtime.time.monotonic", tick)
    client = ScriptedClient([
        assistant(tool_calls=[tool_call("read_file", path="hello.txt")]),
        SystemExit("simulated process death"),
    ])
    with pytest.raises(SystemExit):
        AgentRuntime(client, repository=repository).run(
            MODEL, make_profile(), "Read hello.txt", CountingTools(workspace),
            policy=RunPolicy(max_seconds=100_000),
        )
    [metadata] = repository.list_run_checkpoints()
    checkpoint = repository.get_run_checkpoint(str(metadata["run_id"]))
    assert checkpoint is not None
    spent = float(checkpoint["payload"]["elapsed_seconds"])
    assert spent > 0

    monkeypatch.setattr("ollama_workstation.agents.runtime.time.monotonic", lambda: 5.0)
    resumed_client = ScriptedClient([assistant("should never be requested")])
    result = AgentRuntime(resumed_client, repository=repository).run(
        MODEL, make_profile(), "Read hello.txt", CountingTools(workspace),
        resume_checkpoint=checkpoint, policy=RunPolicy(max_seconds=spent),
    )
    assert result.status == "time_limit"
    assert resumed_client.calls == 0
    assert result.usage["elapsed_seconds"] >= spent
