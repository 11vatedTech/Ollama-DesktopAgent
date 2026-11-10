from ollama_workstation.models.schemas import CapabilityResult
from ollama_workstation.profiling.profiler import ModelProfiler


def test_repeated_probe_aggregation_tracks_pass_rate_and_confidence() -> None:
    result = ModelProfiler._aggregate("instruction_following", [
        CapabilityResult("instruction_following", "passed", 100, 0.2, 1, 1, 120.0,
                         "exact output", ["Trial passed"]),
        CapabilityResult("instruction_following", "failed", 0, 0.2, 1, 0, 140.0,
                         "exact output", ["Unexpected output"]),
        CapabilityResult("instruction_following", "passed", 100, 0.2, 1, 1, 130.0,
                         "exact output", ["Trial passed"]),
    ])
    assert result.status == "partial"
    assert result.score == 200 / 3
    assert result.attempts == 3
    assert result.passed == 2
    assert result.confidence == 0.5
    assert result.duration_ms == 130.0
    assert any("2/3" in item for item in result.limitations)


def test_repeated_unsupported_protocol_stays_unknown_not_zero() -> None:
    result = ModelProfiler._aggregate("tool_calling", [
        CapabilityResult("tool_calling", "unsupported", None, 0.0, 1, 0),
        CapabilityResult("tool_calling", "unsupported", None, 0.0, 1, 0),
    ])
    assert result.status == "unsupported"
    assert result.score is None
    assert result.attempts == 2



def test_aggregated_probe_retains_inference_speed_metrics() -> None:
    outcomes = [
        CapabilityResult("reasoning", "passed", 100, 0.2, 1, 1, 100.0,
                         "smoke", metrics={"generation_tokens_per_second": 20.0}),
        CapabilityResult("reasoning", "passed", 100, 0.2, 1, 1, 120.0,
                         "smoke", metrics={"generation_tokens_per_second": 30.0}),
    ]
    metrics = ModelProfiler._aggregate_metrics(outcomes)
    assert metrics["mean_generation_tokens_per_second"] == 25.0
    assert len(metrics["trials"]) == 2
