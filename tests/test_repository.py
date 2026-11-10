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
