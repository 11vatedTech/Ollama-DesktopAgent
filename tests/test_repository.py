from pathlib import Path

from ollama_workstation.models.schemas import CapabilityResult, ModelProfile
from ollama_workstation.storage.repository import Repository


def test_profiles_round_trip(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "state.sqlite3")
    profile = ModelProfile(
        model_name="qwen:7b", digest="sha256:1", generated_at="2026-11-10",
        professional_title="Precision Software Engineer",
        capabilities=[CapabilityResult("code_generation", "passed", 75, 0.5, 1, 1)])
    repository.save_profile(profile)
    loaded = repository.get_profile("qwen:7b", "sha256:1")
    assert loaded is not None
    assert loaded.professional_title == profile.professional_title
    assert loaded.capabilities[0].score == 75

def test_agent_runs_are_persisted(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "runs.sqlite3")
    repository.save_run("run-1", "model", "Test Agent", "inspect", "completed",
                        "2026-11-10T00:00:00+00:00", "2026-11-10T00:00:01+00:00",
                        [{"kind": "completed", "message": "done"}])
    rows = repository.list_runs()
    assert rows[0]["run_id"] == "run-1"
    assert rows[0]["trace"][0]["kind"] == "completed"



def test_agent_run_events_are_durable_before_run_completion(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "durable-runs.sqlite3")
    repository.append_run_event(
        "run-crash", "local:latest", "Repository Cartographer", "Inspect a project",
        {
            "kind": "started",
            "message": "Agent started.",
            "data": {"model": "local:latest"},
            "timestamp": "2026-11-10T10:00:00+00:00",
        },
    )
    repository.append_run_event(
        "run-crash", "local:latest", "Repository Cartographer", "Inspect a project",
        {
            "kind": "tool_completed",
            "message": "Read README.md.",
            "data": {"path": "README.md"},
            "timestamp": "2026-11-10T10:00:01+00:00",
        },
    )

    # Simulate abrupt termination: no call to save_run() is made.
    run = repository.list_runs()[0]
    events = repository.list_run_events("run-crash")
    assert run["status"] == "running"
    assert [event["kind"] for event in events] == ["started", "tool_completed"]
    assert [event["sequence"] for event in events] == [1, 2]


def test_finalizing_run_preserves_append_only_event_history(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "finalized-runs.sqlite3")
    start = {
        "kind": "started", "message": "Agent started.", "data": {},
        "timestamp": "2026-11-10T10:00:00+00:00",
    }
    complete = {
        "kind": "completed", "message": "Agent completed.", "data": {"verified": True},
        "timestamp": "2026-11-10T10:00:02+00:00",
    }
    repository.append_run_event("run-final", "model", "Agent", "task", start)
    repository.append_run_event("run-final", "model", "Agent", "task", complete)
    repository.save_run(
        "run-final", "model", "Agent", "task", "completed",
        start["timestamp"], complete["timestamp"], [start, complete],
    )
    assert repository.list_runs()[0]["status"] == "completed"
    assert [event["kind"] for event in repository.list_run_events("run-final")] == [
        "started", "completed"
    ]
