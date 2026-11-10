"""Opt-in public model-card research. Search matches are suggestions, never silently trusted."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any

import httpx

from ollama_workstation.models.schemas import Evidence
from ollama_workstation.profiling.model_card import extract_model_card_dossier


@dataclass(slots=True)
class ModelCardCandidate:
    repository_id: str
    pipeline_tag: str | None = None
    downloads: int = 0
    likes: int = 0
    tags: list[str] = field(default_factory=list)
    last_modified: str | None = None
    model_card_url: str = ""
    match_score: float = 0.0
    match_reasons: list[str] = field(default_factory=list)


class HuggingFaceResearcher:
    """Searches public Hugging Face metadata; it does not infer training data."""

    def __init__(self, timeout: float = 12.0) -> None:
        self.timeout = timeout

    @staticmethod
    def _search_term(ollama_name: str) -> str:
        base = ollama_name.split(":", 1)[0]
        return re.sub(r"[^a-zA-Z0-9._-]+", " ", base).strip()

    @staticmethod
    def candidate_match(ollama_name: str, repository_id: str) -> tuple[float, list[str]]:
        """Return a lexical candidate hint, never an automatic identity decision."""
        local_name = ollama_name.split(":", 1)[0].lower()
        upstream_name = repository_id.rsplit("/", 1)[-1].lower()
        def normalize(value: str) -> str:
            return re.sub(r"[^a-z0-9]+", "", value)

        local_normalized = normalize(local_name)
        upstream_normalized = normalize(upstream_name)
        sequence_score = SequenceMatcher(None, local_normalized, upstream_normalized).ratio()
        local_tokens = set(re.findall(r"[a-z]+|[0-9]+", local_name))
        upstream_tokens = set(re.findall(r"[a-z]+|[0-9]+", upstream_name))
        union = local_tokens | upstream_tokens
        token_score = len(local_tokens & upstream_tokens) / len(union) if union else 0.0
        score = round(max(sequence_score, token_score), 3)
        reasons = [
            f"Normalized name similarity: {sequence_score:.0%}",
            f"Token overlap: {token_score:.0%}",
            "Heuristic only; user confirmation is required before attaching this source.",
        ]
        return score, reasons

    def search_candidates(self, ollama_name: str, limit: int = 8) -> list[ModelCardCandidate]:
        term = self._search_term(ollama_name)
        try:
            response = httpx.get(
                "https://huggingface.co/api/models",
                params={"search": term, "limit": max(1, min(limit, 20)),
                        "sort": "downloads", "direction": "-1"},
                timeout=self.timeout,
                headers={"User-Agent": "11vated-Ollama-Workstation/0.2.0"},
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError(f"Public model-card search failed: {exc}") from exc
        result: list[ModelCardCandidate] = []
        for item in data if isinstance(data, list) else []:
            repo_id = str(item.get("id") or "")
            if repo_id:
                match_score, match_reasons = self.candidate_match(ollama_name, repo_id)
                result.append(ModelCardCandidate(
                    repository_id=repo_id, pipeline_tag=item.get("pipeline_tag"),
                    downloads=int(item.get("downloads") or 0), likes=int(item.get("likes") or 0),
                    tags=[str(tag) for tag in item.get("tags", [])[:50]],
                    last_modified=item.get("lastModified"),
                    model_card_url=f"https://huggingface.co/{repo_id}",
                    match_score=match_score, match_reasons=match_reasons,
                ))
        result.sort(key=lambda candidate: (candidate.match_score, candidate.downloads), reverse=True)
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
    dossier = extract_model_card_dossier(card)
    if not dossier.repository_id:
        raise ValueError("Selected model card is missing a canonical repository identifier")

    # Re-attaching an updated card replaces the previous card-derived claims.
    card_claims = {
        "User-selected upstream repository", "Publisher pipeline tag",
        "Model-card license declaration", "Model-card dataset disclosure",
        "Training dataset disclosure", "Publisher-declared base model",
        "Publisher-declared languages", "Publisher model description", "Publisher intended use",
        "Publisher training details", "Publisher evaluation disclosure",
        "Publisher-disclosed limitations", "Model card content hash",
    }
    profile.origin_evidence = [
        item for item in profile.origin_evidence if item.claim not in card_claims
    ]
    url = dossier.model_card_url or f"https://huggingface.co/{dossier.repository_id}"

    def add(claim: str, value: str, *, level: str = "documented", notes: str = "") -> None:
        if value:
            profile.origin_evidence.append(Evidence(
                claim, value, level, "User-selected Hugging Face model card", url, notes=notes
            ))

    add("User-selected upstream repository", dossier.repository_id,
        notes="Repository selected explicitly by the user; the Ollama-to-upstream identity match is not independently guaranteed.")
    add("Publisher pipeline tag", dossier.pipeline_tag)
    add("Model-card license declaration", dossier.license,
        notes="Verify the full license text and restrictions before redistribution.")
    add("Model-card dataset disclosure", ", ".join(dossier.datasets),
        notes="Publisher-declared metadata; not an independently verified or necessarily complete training corpus.")
    if not dossier.datasets:
        add("Training dataset disclosure", "No structured dataset disclosure found",
            level="unknown",
            notes="Absence of disclosure does not establish absence of training data.")
    add("Publisher-declared base model", ", ".join(dossier.base_models))
    add("Publisher-declared languages", ", ".join(dossier.languages))
    add("Publisher model description", dossier.model_description)
    add("Publisher intended use", dossier.intended_use)
    add("Publisher training details", dossier.training_details)
    add("Publisher evaluation disclosure", dossier.evaluation_details)
    add("Publisher-disclosed limitations", dossier.limitations,
        notes="Publisher disclosure; independent limitations require empirical evaluation.")
    add("Model card content hash", dossier.model_card_sha256, level="observed",
        notes="SHA-256 binds this dossier to the retrieved model-card text.")

    # A publisher's explicit task tag/section enriches declared purpose, never empirical scores.
    purpose_parts = []
    if dossier.pipeline_tag:
        purpose_parts.append(f"Publisher pipeline: {dossier.pipeline_tag}.")
    if dossier.intended_use:
        purpose_parts.append("Publisher-stated intended use: " + dossier.intended_use[:1200])
    elif dossier.model_description:
        purpose_parts.append("Publisher model description: " + dossier.model_description[:1200])
    if purpose_parts:
        profile.declared_purpose = " ".join(purpose_parts)

    profile.operational["upstream_model_card"] = {
        "repository_id": dossier.repository_id,
        "metadata_url": dossier.metadata_url,
        "model_card_url": dossier.model_card_url,
        "sha256": dossier.model_card_sha256,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "publisher_tags": dossier.tags,
    }
    if dossier.limitations:
        limitation = "Publisher-disclosed: " + dossier.limitations[:1200]
        if limitation not in profile.known_limitations:
            profile.known_limitations.append(limitation)
    return profile
