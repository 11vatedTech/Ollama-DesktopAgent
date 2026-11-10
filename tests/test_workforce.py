from ollama_workstation.agents.workforce import WorkforceMatcher
from ollama_workstation.models.schemas import CapabilityResult, ModelProfile


def test_workforce_matcher_prefers_evidence_and_exposes_missing_data() -> None:
    profiles = [
        ModelProfile(model_name="coder", digest="1", generated_at="now", professional_title="Coder",
                     capabilities=[CapabilityResult("code_generation", "passed", 90, 0.9),
                                   CapabilityResult("instruction_following", "passed", 85, 0.9)]),
        ModelProfile(model_name="unknown", digest="2", generated_at="now",
                     capabilities=[CapabilityResult("code_generation", "not_assessed")])]
    results = WorkforceMatcher.rank("software_engineering", profiles)
    assert len(results) == 1
    assert results[0].model_name == "coder"
    assert results[0].status == "evidence-backed"
