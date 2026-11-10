from ollama_workstation.models.schemas import ModelProfile
from ollama_workstation.profiling.model_card import extract_model_card_dossier
from ollama_workstation.profiling.research import attach_model_card_evidence


def test_model_card_dossier_extracts_explicit_publisher_disclosures() -> None:
    card = {
        "metadata": {
            "id": "publisher/specialist-model",
            "pipeline_tag": "text-generation",
            "tags": ["license:apache-2.0", "language:en", "language:fr"],
            "cardData": {
                "datasets": ["publisher/dataset-a"],
                "base_model": ["publisher/base-model"],
            },
            "model-index": [{
                "name": "Specialist Model",
                "results": [{
                    "task": {"type": "text-generation"},
                    "dataset": {"name": "CodeBench"},
                    "metrics": [{"name": "pass@1", "value": 0.72}],
                }],
            }],
        },
        "model_card_markdown": (
            "# Model Description\nA model designed for repository-level code analysis.\n"
            "# Intended Use\nUse for code review and repository navigation.\n"
            "# Limitations\nMay produce incorrect patches.\n"
            "# Training Details\nFine-tuned on a disclosed instruction mixture.\n"
            "# Evaluation\nEvaluated on repository repair tasks.\n"
        ),
        "metadata_url": "https://huggingface.co/api/models/publisher/specialist-model",
        "model_card_url": "https://huggingface.co/publisher/specialist-model/raw/main/README.md",
    }
    dossier = extract_model_card_dossier(card)
    assert dossier.repository_id == "publisher/specialist-model"
    assert dossier.pipeline_tag == "text-generation"
    assert dossier.license == "apache-2.0"
    assert dossier.datasets == ["publisher/dataset-a"]
    assert dossier.base_models == ["publisher/base-model"]
    assert dossier.languages == ["en", "fr"]
    assert "repository-level code analysis" in dossier.model_description
    assert "code review" in dossier.intended_use
    assert "incorrect patches" in dossier.limitations
    assert "instruction mixture" in dossier.training_details
    assert "repository repair" in dossier.evaluation_details
    assert "pass@1=0.72" in dossier.published_evaluations[0]
    assert len(dossier.model_card_sha256) == 64


def test_attaching_card_updates_declared_purpose_without_fabricating_scores() -> None:
    profile = ModelProfile(model_name="specialist:latest", digest="sha256:abc", generated_at="now")
    card = {
        "metadata": {
            "id": "publisher/specialist-model",
            "pipeline_tag": "text-generation",
            "cardData": {"datasets": ["dataset-a"]},
        },
        "model_card_markdown": (
            "# Intended Use\nRepository exploration and code review.\n"
            "# Limitations\nNot a substitute for human review.\n"
        ),
    }
    attach_model_card_evidence(profile, card)
    claims = {item.claim: item for item in profile.origin_evidence}
    assert "Publisher intended use" in claims
    assert claims["Publisher intended use"].level == "documented"
    assert "Publisher-stated intended use" in profile.declared_purpose
    assert profile.known_limitations
    assert profile.capabilities == []


def test_missing_dataset_disclosure_remains_unknown() -> None:
    profile = ModelProfile(model_name="opaque:latest", digest="", generated_at="now")
    attach_model_card_evidence(profile, {
        "metadata": {"id": "publisher/opaque", "cardData": {}},
        "model_card_markdown": "# Model Description\nAn opaque model.\n",
    })
    dataset = next(x for x in profile.origin_evidence
                   if x.claim == "Training dataset disclosure")
    assert dataset.level == "unknown"
