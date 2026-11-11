from __future__ import annotations

from ollama_workstation.models.model_intelligence import (
    ModelIntelligence,
    estimate_resource_envelope,
    inspect_ollama_model,
)
from ollama_workstation.models.schemas import ModelRef


def sample_show() -> dict[str, object]:
    return {
        "details": {
            "family": "qwen2",
            "families": ["qwen2"],
            "format": "gguf",
            "parameter_size": "7.6B",
            "quantization_level": "Q4_K_M",
        },
        "capabilities": ["completion", "tools", "insert"],
        "model_info": {
            "general.architecture": "qwen2",
            "general.parameter_count": 7_615_616_512,
            "general.license": "apache-2.0",
            "general.license.link": "https://example.test/LICENSE",
            "general.finetune": "Instruct",
            "general.tags": ["code", "chat"],
            "general.languages": ["en"],
            "general.base_model.0.name": "Qwen2.5-Coder-7B",
            "general.base_model.0.organization": "Qwen",
            "qwen2.context_length": 32768,
            "qwen2.embedding_length": 3584,
            "qwen2.block_count": 28,
            "qwen2.attention.head_count": 28,
            "qwen2.attention.head_count_kv": 4,
        },
        "system": "You are Qwen.",
    }


def test_model_intelligence_preserves_observed_and_inferred_evidence() -> None:
    model = ModelRef("qwen2.5-coder:7b", size_bytes=4_700_000_000, digest="sha256:abc")
    intel = inspect_ollama_model(model, sample_show())
    assert intel.family == "qwen2"
    assert intel.architecture == "qwen2"
    assert intel.parameter_count == 7_615_616_512
    assert intel.quantization_bits == 4.8
    assert intel.context_length == 32768
    assert intel.embedding_length == 3584
    assert intel.supports_tools_declared is True
    assert intel.supports_vision_declared is False
    assert intel.base_models == ("Qwen/Qwen2.5-Coder-7B",)
    assert intel.resource_estimate is not None
    assert intel.resource_estimate.weights_bytes == 4_700_000_000
    assert intel.resource_estimate.kv_cache_bytes and intel.resource_estimate.kv_cache_bytes > 0
    assert any(item["claim"] == "Memory footprint" and item["level"] == "inferred"
               for item in intel.evidence)
    assert any("declared" in limitation.lower() for limitation in intel.limitations)
    restored = ModelIntelligence.from_dict(intel.to_dict())
    assert restored.to_dict()["declared_capabilities"] == ["completion", "tools", "insert"]


def test_resource_estimate_uses_weights_when_kv_geometry_is_unavailable() -> None:
    estimate = estimate_resource_envelope(
        inventory_size_bytes=1000,
        parameter_count=100,
        quantization_bits=4.8,
        block_count=None,
        context_length=4096,
        embedding_length=None,
        head_count=None,
        head_count_kv=None,
        key_length=None,
        value_length=None,
    )
    assert estimate.weights_bytes == 1000
    assert estimate.kv_cache_bytes is None
    assert estimate.total_bytes == 1000
    assert estimate.evidence_level == "inferred"


def test_large_context_increases_the_inferred_kv_envelope() -> None:
    model = ModelRef("vision:latest", size_bytes=3_300_000_000, digest="sha256:v")
    intel = inspect_ollama_model(
        model,
        {
            "details": {"family": "qwen3vl", "parameter_size": "4B", "quantization_level": "Q4_K_M"},
            "capabilities": ["completion", "vision"],
            "model_info": {
                "general.architecture": "qwen3vl",
                "qwen3vl.context_length": 262144,
                "qwen3vl.embedding_length": 4096,
                "qwen3vl.block_count": 36,
                "qwen3vl.attention.head_count": 32,
                "qwen3vl.attention.head_count_kv": 8,
            },
        },
    )
    assert intel.supports_vision_declared is True
    assert intel.resource_estimate is not None
    assert intel.resource_estimate.kv_cache_bytes and intel.resource_estimate.kv_cache_bytes > 1_000_000_000
