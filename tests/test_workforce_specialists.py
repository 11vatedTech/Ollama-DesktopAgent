from ollama_workstation.agents.workforce import WorkforceMatcher
from ollama_workstation.models.schemas import CapabilityResult, ModelProfile


def test_workforce_can_rank_new_specialist_roles() -> None:
    analyst = ModelProfile(
        model_name="analyst", digest="abc", generated_at="now",
        professional_title="Evidence Synthesis Analyst",
        capabilities=[
            CapabilityResult("research", "passed", 90, 0.8, 5, 5),
            CapabilityResult("long_context", "passed", 88, 0.8, 5, 5),
            CapabilityResult("instruction_following", "passed", 92, 0.8, 5, 5),
        ],
    )
    matches = WorkforceMatcher.rank("research", [analyst])
    assert len(matches) == 1
    assert matches[0].model_name == "analyst"
    assert matches[0].status == "evidence-backed"


def test_unknown_specialist_capabilities_are_not_ranked_as_zero() -> None:
    unknown = ModelProfile(model_name="unknown", digest="", generated_at="now")
    assert WorkforceMatcher.rank("creative", [unknown]) == []
