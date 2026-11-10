from ollama_workstation.models.identity import assign_identity
from ollama_workstation.models.schemas import CapabilityResult, ModelProfile


def test_identity_uses_observed_capabilities_and_selects_role() -> None:
    profile = ModelProfile(model_name="unit-test", digest="abc", generated_at="now")
    profile.capabilities = [
        CapabilityResult("code_generation", "passed", 90, 0.9, 4, 4),
        CapabilityResult("code_comprehension", "passed", 92, 0.9, 4, 4),
        CapabilityResult("instruction_following", "passed", 90, 0.8, 4, 4),
        CapabilityResult("tool_calling", "failed", 10, 0.4, 4, 0),
    ]
    assign_identity(profile)
    assert profile.professional_title == "Precision Software Engineer"
    assert profile.agentability_mode in {"guided", "structured_actions"}

def test_untested_model_is_not_given_a_certified_role() -> None:
    profile = ModelProfile(model_name="unprofiled", digest="", generated_at="now")
    assign_identity(profile)
    assert profile.professional_title == "Unclassified Model"
    assert profile.agentability_status == "not_assessed"
    assert profile.overall_reliability is None
