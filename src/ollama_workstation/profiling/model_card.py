"""Structured, provenance-preserving extraction from explicitly selected model cards.

The parser extracts publisher-declared information; it does not infer undisclosed
training data or treat model-card prose as empirical capability evidence.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ModelCardDossier:
    repository_id: str = ""
    pipeline_tag: str = ""
    license: str = ""
    base_models: list[str] = field(default_factory=list)
    datasets: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    model_description: str = ""
    intended_use: str = ""
    limitations: str = ""
    training_details: str = ""
    evaluation_details: str = ""
    published_evaluations: list[str] = field(default_factory=list)
    model_card_sha256: str = ""
    metadata_url: str = ""
    model_card_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return [str(item).strip() for item in values if str(item).strip()]


def _sections(markdown: str) -> dict[str, str]:
    """Extract Markdown heading sections without interpreting their contents."""
    result: dict[str, str] = {}
    current = ""
    chunks: list[str] = []
    for line in markdown.splitlines():
        heading = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            if current:
                result[current] = "\n".join(chunks).strip()
            current = re.sub(r"[^a-z0-9]+", " ", heading.group(1).lower()).strip()
            chunks = []
        elif current:
            chunks.append(line)
    if current:
        result[current] = "\n".join(chunks).strip()
    return result


def _section(sections: dict[str, str], *needles: str) -> str:
    for heading, body in sections.items():
        if any(needle in heading for needle in needles) and body.strip():
            return body.strip()[:6000]
    return ""


def _tag_value(tags: list[str], prefix: str) -> list[str]:
    return [tag.split(":", 1)[1] for tag in tags if tag.lower().startswith(prefix)]


def extract_model_card_dossier(card: dict[str, Any]) -> ModelCardDossier:
    """Extract only explicit metadata and clearly labelled card sections."""
    metadata = card.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    card_data = metadata.get("cardData") or {}
    if not isinstance(card_data, dict):
        card_data = {}
    raw_tags = metadata.get("tags") or []
    tags = [str(tag) for tag in raw_tags if isinstance(tag, (str, int, float))]
    markdown = str(card.get("model_card_markdown") or "")
    sections = _sections(markdown)
    repo_id = str(metadata.get("id") or "")
    license_value = card_data.get("license") or next(iter(_tag_value(tags, "license:")), "")
    languages = _strings(card_data.get("language")) or _tag_value(tags, "language:")
    datasets = _strings(card_data.get("datasets")) or _tag_value(tags, "dataset:")
    base_models = _strings(card_data.get("base_model"))
    pipeline = str(metadata.get("pipeline_tag") or card_data.get("pipeline_tag") or "")
    raw_index = metadata.get("model-index") or card_data.get("model-index") or []
    published_evaluations: list[str] = []
    for model_entry in raw_index if isinstance(raw_index, list) else []:
        if not isinstance(model_entry, dict):
            continue
        model_name = str(model_entry.get("name") or repo_id or "publisher model")
        raw_results = model_entry.get("results") or []
        if not isinstance(raw_results, list):
            continue
        for result in raw_results[:30]:
            if not isinstance(result, dict):
                continue
            dataset = result.get("dataset") or {}
            task = result.get("task") or {}
            if not isinstance(dataset, dict):
                dataset = {}
            if not isinstance(task, dict):
                task = {}
            dataset_name = str(dataset.get("name") or dataset.get("type") or "unspecified dataset")
            task_name = str(task.get("name") or task.get("type") or "unspecified task")
            metrics = result.get("metrics") or []
            if not isinstance(metrics, list):
                metrics = []
            metric_text = ", ".join(
                f"{metric.get('name', 'metric')}={metric.get('value', 'n/a')}"
                for metric in metrics[:10] if isinstance(metric, dict)
            )
            published_evaluations.append(
                f"{model_name}: {task_name} on {dataset_name}"
                + (f" ({metric_text})" if metric_text else "")
            )
    digest = str(card.get("model_card_sha256") or hashlib.sha256(markdown.encode("utf-8")).hexdigest())
    return ModelCardDossier(
        repository_id=repo_id,
        pipeline_tag=pipeline,
        license=str(license_value or ""),
        base_models=base_models,
        datasets=datasets,
        languages=languages,
        tags=tags[:100],
        model_description=_section(sections, "model description", "model overview", "description"),
        intended_use=_section(sections, "intended use", "use cases", "intended purpose", "uses"),
        limitations=_section(sections, "limitations", "risks", "bias"),
        training_details=_section(sections, "training details", "training procedure", "training data"),
        evaluation_details=_section(sections, "evaluation", "benchmark", "results"),
        published_evaluations=published_evaluations,
        model_card_sha256=digest,
        metadata_url=str(card.get("metadata_url") or (
            f"https://huggingface.co/api/models/{repo_id}" if repo_id else "")),
        model_card_url=str(card.get("model_card_url") or (
            f"https://huggingface.co/{repo_id}" if repo_id else "")),
    )
