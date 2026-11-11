"""Opt-in real Ollama integration tests.

Run with ``OLLAMA_LIVE=1``. These tests are deliberately excluded from the
normal suite because they load local models and depend on the installed Ollama
inventory. They are evidence, not a mock substitute, for the provider adapter.
"""

from __future__ import annotations

import os

import pytest

from ollama_workstation.models.model_intelligence import inspect_ollama_model
from ollama_workstation.models.schemas import ModelRef
from ollama_workstation.ollama.client import OllamaClient

pytestmark = pytest.mark.skipif(
    os.environ.get("OLLAMA_LIVE") != "1",
    reason="set OLLAMA_LIVE=1 to exercise the configured local Ollama instance",
)

MODEL = os.environ.get("OLLAMA_LIVE_MODEL", "qwen2.5-coder:7b")
EMBED_MODEL = os.environ.get("OLLAMA_LIVE_EMBED_MODEL", "nomic-embed-text:latest")


def _client() -> OllamaClient:
    return OllamaClient(
        os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        timeout=120.0,
    )


def _model_ref(client: OllamaClient) -> ModelRef:
    for item in client.list_models():
        if str(item.get("name") or "") == MODEL:
            return ModelRef(
                MODEL,
                size_bytes=int(item.get("size") or 0),
                digest=str(item.get("digest") or ""),
                modified_at=str(item.get("modified_at") or ""),
                details=item.get("details") if isinstance(item.get("details"), dict) else {},
            )
    pytest.skip(f"{MODEL!r} is not installed in the configured Ollama instance")


def test_live_version_inventory_and_metadata() -> None:
    client = _client()
    assert client.health()
    assert client.version()
    ref = _model_ref(client)
    show = client.show_model(MODEL)
    intelligence = inspect_ollama_model(ref, show)
    assert intelligence.family
    assert intelligence.declared_capabilities
    assert intelligence.evidence


def test_live_chat_completion_is_nonempty() -> None:
    client = _client()
    _model_ref(client)
    response = client.chat(
        MODEL,
        [{"role": "user", "content": "Reply with the single word READY."}],
        options={"temperature": 0, "num_predict": 16},
        keep_alive="30s",
    )
    message = response.get("message")
    assert isinstance(message, dict)
    assert str(message.get("content") or "").strip()


def test_live_embeddings_are_real_vectors() -> None:
    client = _client()
    names = {str(item.get("name") or "") for item in client.list_models()}
    if EMBED_MODEL not in names:
        pytest.skip(f"{EMBED_MODEL!r} is not installed")
    vectors = client.embed(EMBED_MODEL, ["local intelligence", "routing evidence"])
    assert len(vectors) == 2
    assert all(len(vector) > 32 for vector in vectors)
    assert all(all(isinstance(value, float) for value in vector[:4]) for vector in vectors)
