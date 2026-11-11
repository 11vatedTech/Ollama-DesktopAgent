from __future__ import annotations

from ollama_workstation.models.model_intelligence import inspect_ollama_model
from ollama_workstation.models.routing import ModelRouter, NoEligibleModel, RoutingRequest
from ollama_workstation.models.schemas import CapabilityResult, ModelProfile, ModelRef

HOST = {
    "system_memory": {"available_bytes": 32 * 1024**3},
    "nvidia_gpus": [{"memory_free_mib": 12_000}],
}


def model(name: str, *, context: int = 8192, size: int = 4_000_000_000,
          capabilities: list[str] | None = None, kv_heads: int = 8) -> tuple[ModelRef, object]:
    ref = ModelRef(name, size_bytes=size, digest=f"sha256:{name}")
    intel = inspect_ollama_model(ref, {
        "details": {"family": "test", "parameter_size": "7B", "quantization_level": "Q4_K_M"},
        "capabilities": capabilities or ["completion"],
        "model_info": {
            "general.architecture": "test",
            "test.context_length": context,
            "test.embedding_length": 4096,
            "test.block_count": 32,
            "test.attention.head_count": 32,
            "test.attention.head_count_kv": kv_heads,
        },
    })
    return ref, intel


def profile(name: str, *results: CapabilityResult, speed: float | None = None) -> ModelProfile:
    return ModelProfile(
        model_name=name, digest=f"sha256:{name}", generated_at="now",
        capabilities=list(results),
        operational={"performance_summary": {"mean_generation_tokens_per_second": speed}}
        if speed is not None else {},
    )


def test_router_prefers_verified_capability_and_rejects_declared_only_when_required() -> None:
    coder, coder_intel = model("coder:7b", capabilities=["completion", "tools"])
    chat, chat_intel = model("chat:7b")
    profiles = [
        profile("coder:7b", CapabilityResult("tool_calling", "passed", 92, 0.8),
                CapabilityResult("instruction_following", "passed", 88, 0.8)),
        profile("chat:7b", CapabilityResult("instruction_following", "passed", 80, 0.8)),
    ]
    decision = ModelRouter().rank(
        [coder, chat],
        request=RoutingRequest(required_capabilities=("tool_calling",), require_verified=True),
        profiles=profiles,
        intelligence={"coder:7b": coder_intel, "chat:7b": chat_intel},
        host=HOST,
    )
    assert decision.selected is not None
    assert decision.selected.model_name == "coder:7b"
    assert decision.selected.verified_capabilities == ("instruction_following", "tool_calling")
    chat_route = next(item for item in decision.candidates if item.model_name == "chat:7b")
    assert chat_route.eligible is False
    assert "tool_calling" in chat_route.missing_capabilities


def test_declared_metadata_allows_provisional_routing_but_marks_it_unverified() -> None:
    coder, coder_intel = model("coder:7b", capabilities=["completion", "tools"])
    decision = ModelRouter().rank(
        [coder],
        request=RoutingRequest(required_capabilities=("tool_calling",)),
        intelligence={"coder:7b": coder_intel},
        host=HOST,
    )
    assert decision.selected is not None
    assert decision.selected.details["declared_only"] is True
    assert "unverified" in decision.selected.reason


def test_router_rejects_context_and_memory_constraints() -> None:
    small, small_intel = model("small:7b", context=4096, size=100_000_000_000)
    decision = ModelRouter().rank(
        [small],
        request=RoutingRequest(context_tokens=8192),
        intelligence={"small:7b": small_intel},
        host=HOST,
    )
    assert decision.selected is None
    assert "context capacity" in decision.candidates[0].reason

    decision = ModelRouter().rank(
        [small],
        request=RoutingRequest(max_memory_bytes=1_000),
        intelligence={"small:7b": small_intel},
        host=HOST,
    )
    assert decision.selected is None
    assert "estimated memory" in decision.candidates[0].reason


def test_router_excludes_embedding_only_artifact_for_completion() -> None:
    embed, embed_intel = model("embed:latest", size=300_000_000, capabilities=["embedding"])
    decision = ModelRouter().rank(
        [embed],
        request=RoutingRequest(),
        intelligence={"embed:latest": embed_intel},
        host=HOST,
    )
    assert decision.selected is None
    assert "completion" in decision.candidates[0].reason


def test_router_speed_priority_and_preferred_fallback_chain() -> None:
    fast, fast_intel = model("fast:7b", size=3_000_000_000)
    slow, slow_intel = model("slow:7b", size=3_000_000_000)
    profiles = [
        profile("fast:7b", CapabilityResult("instruction_following", "passed", 80, 0.8), speed=55),
        profile("slow:7b", CapabilityResult("instruction_following", "passed", 80, 0.8), speed=5),
    ]
    decision = ModelRouter().rank(
        [slow, fast],
        request=RoutingRequest(preferred_models=("slow:7b",), speed_priority=0.8),
        profiles=profiles,
        intelligence={"fast:7b": fast_intel, "slow:7b": slow_intel},
        host=HOST,
    )
    assert len(decision.fallback_chain) == 2
    assert decision.selected is not None
    assert decision.selected.model_name in {"fast:7b", "slow:7b"}


def test_no_eligible_model_exposes_explanations() -> None:
    embed, embed_intel = model("embed:latest", capabilities=["embedding"])
    try:
        ModelRouter().select(
            [embed], request=RoutingRequest(), intelligence={"embed:latest": embed_intel}
        )
    except NoEligibleModel as exc:
        assert "completion" in str(exc)
        assert exc.candidates
    else:
        raise AssertionError("expected NoEligibleModel")
