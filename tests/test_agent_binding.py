import pytest

from ollama_workstation.agents.binding import validate_installed_model_binding
from ollama_workstation.models.schemas import ModelProfile, ModelRef


def test_profile_binding_accepts_exact_installed_digest() -> None:
    profile = ModelProfile(model_name="qwen2.5-coder:7b", digest="sha256:abc", generated_at="now")
    installed = ModelRef(name="qwen2.5-coder:7b", digest="sha256:abc")
    validate_installed_model_binding(profile, installed)


def test_profile_binding_rejects_replaced_model_artifact() -> None:
    profile = ModelProfile(model_name="qwen2.5-coder:7b", digest="sha256:old", generated_at="now")
    installed = ModelRef(name="qwen2.5-coder:7b", digest="sha256:new")
    with pytest.raises(ValueError, match="digest changed"):
        validate_installed_model_binding(profile, installed)


def test_profile_binding_fails_closed_when_digest_is_unavailable() -> None:
    profile = ModelProfile(model_name="qwen2.5-coder:7b", digest="", generated_at="now")
    installed = ModelRef(name="qwen2.5-coder:7b", digest="")
    with pytest.raises(ValueError, match="digest is missing"):
        validate_installed_model_binding(profile, installed)


def test_profile_binding_rejects_name_mismatch() -> None:
    profile = ModelProfile(model_name="model-a:latest", digest="sha256:a", generated_at="now")
    installed = ModelRef(name="model-b:latest", digest="sha256:a")
    with pytest.raises(ValueError, match="not installed model"):
        validate_installed_model_binding(profile, installed)
