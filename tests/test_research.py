from ollama_workstation.models.schemas import ModelProfile
from ollama_workstation.profiling.research import attach_model_card_evidence

def test_model_card_evidence_preserves_origin_and_training_unknown() -> None:
    profile = ModelProfile(model_name="example:latest", digest="abc", generated_at="now")
    card = {
        "metadata": {
            "id": "publisher/example-model",
            "pipeline_tag": "text-generation",
            "tags": ["license:apache-2.0"],
            "cardData": {"license": "apache-2.0", "datasets": ["public-dataset-a"],
                         "base_model": "publisher/base-model"}},
        "model_card_markdown": "# Example\nTraining data details are described by publisher.",
        "model_card_url": "https://huggingface.co/publisher/example-model/raw/main/README.md",
    }
    attach_model_card_evidence(profile, card)
    values = {entry.claim: entry.value for entry in profile.origin_evidence}
    assert values["User-selected upstream repository"] == "publisher/example-model"
    assert values["Model-card dataset disclosure"] == "public-dataset-a"
    assert values["Publisher-declared base model"] == "publisher/base-model"
    assert values["Model-card license declaration"] == "apache-2.0"
