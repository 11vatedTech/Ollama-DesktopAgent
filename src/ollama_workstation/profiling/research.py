"""Opt-in public model-card research. Search matches are suggestions, never silently trusted."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass(slots=True)
class ModelCardCandidate:
    repository_id: str
    pipeline_tag: str | None = None
    downloads: int = 0
    likes: int = 0
    tags: list[str] = field(default_factory=list)
    last_modified: str | None = None
    model_card_url: str = ""


class HuggingFaceResearcher:
    """Searches public Hugging Face metadata; it does not infer training data."""

    def __init__(self, timeout: float = 12.0) -> None:
        self.timeout = timeout

    @staticmethod
    def _search_term(ollama_name: str) -> str:
        base = ollama_name.split(":", 1)[0]
        return re.sub(r"[^a-zA-Z0-9._-]+", " ", base).strip()

    def search_candidates(self, ollama_name: str, limit: int = 8) -> list[ModelCardCandidate]:
        term = self._search_term(ollama_name)
        try:
            response = httpx.get(
                "https://huggingface.co/api/models",
                params={"search": term, "limit": max(1, min(limit, 20)),
                        "sort": "downloads", "direction": "-1"},
                timeout=self.timeout,
                headers={"User-Agent": "11vated-Ollama-Workstation/0.1.0"},
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError(f"Public model-card search failed: {exc}") from exc
        result: list[ModelCardCandidate] = []
        for item in data if isinstance(data, list) else []:
            repo_id = str(item.get("id") or "")
            if repo_id:
                result.append(ModelCardCandidate(
                    repository_id=repo_id, pipeline_tag=item.get("pipeline_tag"),
                    downloads=int(item.get("downloads") or 0), likes=int(item.get("likes") or 0),
                    tags=[str(tag) for tag in item.get("tags", [])[:50]],
                    last_modified=item.get("lastModified"),
                    model_card_url=f"https://huggingface.co/{repo_id}",
                ))
        return result

    def fetch_model_card(self, repository_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository_id):
            raise ValueError("Expected a Hugging Face repository identifier such as org/model")
        api = f"https://huggingface.co/api/models/{repository_id}"
        readme = f"https://huggingface.co/{repository_id}/raw/main/README.md"
        try:
            metadata_response = httpx.get(api, timeout=self.timeout)
            metadata_response.raise_for_status()
            metadata = metadata_response.json()
            card_response = httpx.get(readme, timeout=self.timeout)
            card_response.raise_for_status()
            card_text = card_response.text[:100_000]
            return {"metadata": metadata, "model_card_markdown": card_text,
                    "model_card_sha256": hashlib.sha256(card_text.encode("utf-8")).hexdigest(),
                    "metadata_url": api, "model_card_url": readme}
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError(f"Unable to retrieve model card {repository_id}: {exc}") from exc


def attach_model_card_evidence(profile: Any, card: dict[str, Any]) -> Any:
    """Attach a user-selected upstream card without inventing undisclosed training facts."""
    from ollama_workstation.models.schemas import Evidence

    metadata = card.get("metadata") or {}
    card_data = metadata.get("cardData") or {}
    tags = metadata.get("tags") or []
    repo_id = str(metadata.get("id") or "")
    url = f"https://huggingface.co/{repo_id}" if repo_id else str(card.get("model_card_url") or "")
    if not repo_id or not url:
        raise ValueError("Selected model card is missing a canonical repository identifier")
    profile.origin_evidence.append(Evidence(
        "User-selected upstream repository", repo_id, "documented", "Hugging Face Model Hub", url,
        notes="Repository selected explicitly by the user; identity match is not independently guaranteed."))
    pipeline = metadata.get("pipeline_tag") or card_data.get("pipeline_tag")
    if pipeline:
        profile.origin_evidence.append(Evidence(
            "Publisher pipeline tag", str(pipeline), "documented", "Hugging Face Model Hub", url))
    license_name = card_data.get("license")
    if not license_name:
        license_name = next((str(tag).split(":", 1)[1] for tag in tags
                             if str(tag).startswith("license:")), None)
    if license_name:
        profile.origin_evidence.append(Evidence(
            "Model-card license declaration", str(license_name), "documented", "Hugging Face Model Hub", url,
            notes="Verify full license text and restrictions before redistribution."))
    datasets = card_data.get("datasets")
    if datasets:
        profile.origin_evidence.append(Evidence(
            "Model-card dataset disclosure", ", ".join(str(x) for x in datasets),
            "documented", "Hugging Face model card", url,
            notes="Publisher-declared metadata; not an independently verified complete corpus."))
    else:
        profile.origin_evidence.append(Evidence(
            "Training dataset disclosure", "No structured dataset disclosure found",
            "unknown", "Hugging Face model card", url,
            notes="Absence of metadata does not establish absence of training data."))
    base_model = card_data.get("base_model")
    if base_model:
        values = base_model if isinstance(base_model, list) else [base_model]
        profile.origin_evidence.append(Evidence(
            "Publisher-declared base model", ", ".join(str(x) for x in values),
            "documented", "Hugging Face model card", url))
    card_text = str(card.get("model_card_markdown") or "")
    profile.origin_evidence.append(Evidence(
        "Model card content hash",
        str(card.get("model_card_sha256") or hashlib.sha256(card_text.encode("utf-8")).hexdigest()),
        "observed", "Hugging Face public model-card content",
        str(card.get("model_card_url") or url),
        notes="Hash binds the local dossier to the retrieved model-card text."))
    return profile
