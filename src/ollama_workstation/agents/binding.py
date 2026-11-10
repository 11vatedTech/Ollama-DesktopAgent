"""Fail-closed checks for model profile to installed Ollama artifact binding."""

from __future__ import annotations

from ollama_workstation.models.schemas import ModelProfile, ModelRef


def validate_installed_model_binding(profile: ModelProfile, installed: ModelRef) -> None:
    """Reject execution when the saved profile does not match the installed artifact digest."""
    if installed.name != profile.model_name:
        raise ValueError(
            f"Profile is for {profile.model_name!r}, not installed model {installed.name!r}. "
            "Select the matching profile and model."
        )
    if not profile.digest or not installed.digest:
        raise ValueError(
            "Cannot verify the saved profile against the installed model because a digest is missing. "
            "Run profiling again after checking the Ollama installation."
        )
    if profile.digest != installed.digest:
        raise ValueError(
            "The installed Ollama model digest changed since this profile was created. "
            "Run 11vated profile MODEL again before executing an agent."
        )
