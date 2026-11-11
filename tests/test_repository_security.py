from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ollama_workstation.models.schemas import ModelProfile
from ollama_workstation.storage.repository import Repository


def profile() -> ModelProfile:
    return ModelProfile(model_name="m", digest="sha256:m", generated_at="now",
                        professional_title="Agent")


def save_checkpoint(repository: Repository, payload: dict[str, object], *, updated_at: str) -> None:
    repository.save_run_checkpoint(
        run_id="run", model_name="m", model_digest="sha256:m", agent_title="Agent",
        workspace="C:/workspace", harness_mode="guided", tool_manifest_sha256="manifest",
        state="ready", step=0, tool_calls=0, payload=payload,
        created_at=updated_at, updated_at=updated_at,
    )


def test_checkpoint_payload_is_encrypted_at_rest_and_key_is_separate(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "state.sqlite3")
    payload = {
        "protocol": 2, "task": "secret task", "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "PASSWORD=do-not-store-this"},
        ], "pending_operations": [], "step_budget": 4, "resume_count": 0,
    }
    save_checkpoint(repository, payload, updated_at="2026-11-11T00:00:00+00:00")
    with sqlite3.connect(repository.database) as connection:
        raw = connection.execute("SELECT payload_json FROM agent_run_checkpoints").fetchone()[0]
    assert raw.startswith("enc:v1:")
    assert "do-not-store-this" not in raw
    assert repository.checkpoint_key_path.is_file()
    assert repository.checkpoint_key_path.stat().st_size == 32
    loaded = repository.get_run_checkpoint("run")
    assert loaded is not None
    assert loaded["payload"] == payload


def test_reopening_repository_can_decrypt_checkpoint(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "state.sqlite3")
    save_checkpoint(repository, {
        "protocol": 2, "task": "t", "messages": [
            {"role": "system", "content": "s"}, {"role": "user", "content": "u"},
        ], "step_budget": 4, "resume_count": 0,
    }, updated_at="2026-11-11T00:00:00+00:00")
    reopened = Repository(tmp_path / "state.sqlite3")
    loaded = reopened.get_run_checkpoint("run")
    assert loaded is not None
    assert loaded["payload"]["task"] == "t"


def test_event_journal_hash_chain_detects_tampering(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "state.sqlite3")
    repository.append_run_event("run", "m", "Agent", "task", {
        "kind": "started", "message": "started", "data": {"a": 1},
        "timestamp": "2026-11-11T00:00:00+00:00",
    })
    repository.append_run_event("run", "m", "Agent", "task", {
        "kind": "completed", "message": "completed", "data": {"a": 1},
        "timestamp": "2026-11-11T00:00:01+00:00",
    })
    assert repository.verify_run_event_chain("run", strict=True)
    with sqlite3.connect(repository.database) as connection:
        connection.execute(
            "UPDATE agent_run_events SET message='tampered' WHERE run_id='run' AND sequence=2"
        )
    assert not repository.verify_run_event_chain("run", strict=True)


def test_workforce_run_and_event_chain_are_durable(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "state.sqlite3")
    repository.create_workforce_run("wf", "objective", {"objective": "objective", "tasks": []})
    repository.append_workforce_event("wf", {
        "kind": "started", "message": "started", "data": {"task": "a"},
        "timestamp": "2026-11-11T00:00:00+00:00",
    })
    repository.append_workforce_event("wf", {
        "kind": "completed", "message": "completed", "data": {"task": "a"},
        "timestamp": "2026-11-11T00:00:01+00:00",
    })
    assert len(repository.list_workforce_events("wf")) == 2
    assert repository.verify_workforce_event_chain("wf", strict=True)
    assert repository.get_workforce_run("wf")["status"] == "planned"
    repository.save_workforce_result("wf", "completed", {"ok": True})
    assert repository.get_workforce_run("wf")["result"] == {"ok": True}


def test_run_lease_prevents_two_owners_and_allows_expiry_reclaim(tmp_path: Path) -> None:
    first = Repository(tmp_path / "state.sqlite3")
    second = Repository(tmp_path / "state.sqlite3")
    assert first.acquire_run_lease("run", "owner-a", ttl_seconds=60)
    assert not second.acquire_run_lease("run", "owner-b", ttl_seconds=60)
    assert second.get_run_lease("run")["owner_id"] == "owner-a"
    assert first.renew_run_lease("run", "owner-a", ttl_seconds=60)
    assert not second.release_run_lease("run", "owner-b")
    assert first.release_run_lease("run", "owner-a")
    assert second.acquire_run_lease("run", "owner-b", ttl_seconds=60)


def test_run_lease_is_atomic_under_concurrent_claimants(tmp_path: Path) -> None:
    repositories = [Repository(tmp_path / "state.sqlite3") for _ in range(8)]

    def claim(index: int) -> bool:
        return repositories[index].acquire_run_lease("race", f"owner-{index}", ttl_seconds=60)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, range(8)))
    assert sum(results) == 1
    lease = repositories[0].get_run_lease("race")
    assert lease is not None and lease["active"] is True


def test_repository_profiles_round_trip_with_intelligence(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "state.sqlite3")
    selected = profile()
    selected.intelligence = {"family": "qwen2", "declared_capabilities": ["completion"]}
    repository.save_profile(selected)
    loaded = repository.get_profile("m", "sha256:m")
    assert loaded is not None
    assert loaded.intelligence["family"] == "qwen2"
