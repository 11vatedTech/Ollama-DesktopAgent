from ollama_workstation.agents.identity_package import build_identity_package
from ollama_workstation.models.schemas import CapabilityResult, ModelProfile


def test_identity_package_preserves_capability_trial_counts() -> None:
    profile = ModelProfile(model_name="coder:latest", digest="sha256:abc", generated_at="now")
    profile.capabilities = [
        CapabilityResult("code_generation", "partial", 66.7, 0.5, 3, 2,
                         method="repeated smoke probe")
    ]
    package = build_identity_package(profile)
    capability = package["execution"]["capabilities"][0]
    assert capability["attempts"] == 3
    assert capability["passed"] == 2
    assert capability["score"] == 66.7
