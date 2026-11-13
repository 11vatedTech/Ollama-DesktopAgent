from ollama_workstation.models.identity import assign_identity
from ollama_workstation.models.schemas import CapabilityResult, ModelProfile


def test_research_role_requires_observed_research_capability() -> None:
    profile = ModelProfile(model_name="researcher:latest", digest="abc", generated_at="now")
    profile.capabilities = [
        CapabilityResult("research", "passed", 91, 0.8, 5, 4),
        CapabilityResult("long_context", "passed", 88, 0.8, 5, 4),
        CapabilityResult("instruction_following", "passed", 90, 0.9, 5, 5),
    ]
    assign_identity(profile)
    assert profile.professional_title == "Evidence Synthesis Analyst"
    assert "research" in profile.recommended_roles
    assert profile.agentability_status == "provisional"


def test_publisher_purpose_alone_never_assigns_a_specialist_title() -> None:
    profile = ModelProfile(
        model_name="publisher-coder:latest",
        digest="abc",
        generated_at="now",
        declared_purpose="Publisher says this is a coding model.",
    )
    assign_identity(profile)
    assert profile.professional_title == "Unclassified Model"
    assert profile.identity_confidence == 0.0


def test_title_is_specialized_by_relative_observed_strength() -> None:
    profile = ModelProfile(model_name="repo:latest", digest="abc", generated_at="now")
    profile.capabilities = [
        CapabilityResult("code_comprehension", "passed", 96, 0.9, 5, 5),
        CapabilityResult("code_generation", "passed", 72, 0.9, 5, 4),
        CapabilityResult("instruction_following", "passed", 90, 0.9, 5, 5),
    ]
    assign_identity(profile)
    assert profile.professional_title == "Repository Cartographer"
