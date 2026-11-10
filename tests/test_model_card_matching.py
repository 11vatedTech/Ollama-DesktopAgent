from ollama_workstation.profiling.research import HuggingFaceResearcher


def test_model_card_candidate_match_is_a_hint_not_a_verified_identity() -> None:
    score, reasons = HuggingFaceResearcher.candidate_match(
        "qwen2.5-coder:7b-instruct-q4_K_M",
        "Qwen/Qwen2.5-Coder-7B-Instruct",
    )
    wrong_score, _ = HuggingFaceResearcher.candidate_match(
        "qwen2.5-coder:7b-instruct-q4_K_M",
        "unrelated/vision-language-chat-model",
    )
    assert 0.0 <= score <= 1.0
    assert score > wrong_score
    assert any("user confirmation" in reason for reason in reasons)
