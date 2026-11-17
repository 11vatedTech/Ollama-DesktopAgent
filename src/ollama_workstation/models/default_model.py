"""Choose the model a new conversation starts with.

Ollama lists models newest first, so "the first model" is whatever was pulled last: on
the reference workstation that was a 26B model that cannot load on its 12 GB GPU, and
a first message failed (rendered QA, session 9). The default is instead the model
with the strongest agent evidence that will actually load.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ollama_workstation.models.schemas import ModelRef

_STATUS_RANK = {"qualified": 2, "provisional": 1}
# Weight file plus runtime buffers and a working context; matches the admission
# controller's order of magnitude without importing it into the UI path.
_LOAD_FACTOR = 1.25


def _is_embedding(model: ModelRef) -> bool:
    family = str((model.details or {}).get("family") or "").lower()
    return "embed" in model.name.lower() or family in {"bert", "nomic-bert"}


def preferred_model(models: Iterable[ModelRef],
                    qualifications: Iterable[Mapping[str, Any]],
                    vram_bytes: int | None = None) -> str | None:
    """Best default: qualified > provisional > fits the GPU > anything, never embeddings.

    Qualification counts only for the exact installed digest. Ties keep Ollama's order.
    """
    candidates = [item for item in models if not _is_embedding(item)]
    if not candidates:
        return None
    best: dict[tuple[str, str], int] = {}
    for record in qualifications:
        key = (str(record.get("model_name") or ""), str(record.get("model_digest") or ""))
        rank = _STATUS_RANK.get(str(record.get("status") or ""), 0)
        best[key] = max(best.get(key, 0), rank)

    def fits(model: ModelRef) -> bool:
        if not vram_bytes or not model.size_bytes:
            return True  # unknown: do not penalise
        return model.size_bytes * _LOAD_FACTOR <= vram_bytes

    def score(indexed: tuple[int, ModelRef]) -> tuple[int, int, int]:
        index, model = indexed
        return (best.get((model.name, model.digest), 0), int(fits(model)), -index)

    return max(enumerate(candidates), key=score)[1].name
