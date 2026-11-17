"""The chat's default model is one that will load and has agent evidence (rendered QA)."""

from __future__ import annotations

from ollama_workstation.models.default_model import preferred_model
from ollama_workstation.models.schemas import ModelRef

GIB = 1024 ** 3
# Ollama's order: newest first. The newest here cannot load on a 12 GB GPU.
MODELS = [
    ModelRef("gemma4:26b", size_bytes=18 * GIB, digest="g"),
    ModelRef("qwen3.5:9b", size_bytes=int(6.6 * GIB), digest="q"),
    ModelRef("nomic-embed-text:latest", size_bytes=GIB // 4, digest="n",
             details={"family": "nomic-bert"}),
    ModelRef("qwen2.5-coder:7b", size_bytes=int(4.7 * GIB), digest="c"),
]


def test_qualified_model_wins_over_newest() -> None:
    records = [{"model_name": "qwen3.5:9b", "model_digest": "q", "status": "qualified"},
               {"model_name": "qwen2.5-coder:7b", "model_digest": "c", "status": "provisional"}]
    assert preferred_model(MODELS, records, 12 * GIB) == "qwen3.5:9b"


def test_qualification_must_match_the_installed_digest() -> None:
    stale = [{"model_name": "qwen3.5:9b", "model_digest": "old", "status": "qualified"},
             {"model_name": "qwen2.5-coder:7b", "model_digest": "c", "status": "provisional"}]
    assert preferred_model(MODELS, stale, 12 * GIB) == "qwen2.5-coder:7b"


def test_without_evidence_a_model_that_fits_beats_the_newest() -> None:
    assert preferred_model(MODELS, [], 12 * GIB) == "qwen3.5:9b"
    # Unknown VRAM: nothing is penalised, so Ollama's order stands.
    assert preferred_model(MODELS, [], None) == "gemma4:26b"


def test_embedding_models_are_never_the_default() -> None:
    assert preferred_model([MODELS[2]], [], None) is None
    assert preferred_model([], [], None) is None
