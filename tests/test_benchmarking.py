from __future__ import annotations

from threading import Event
from typing import Any

import pytest

from ollama_workstation.benchmarking.catalog import CORE_SUITE, get_suite
from ollama_workstation.benchmarking.models import BenchmarkCase, BenchmarkSuite
from ollama_workstation.benchmarking.runner import BenchmarkCancelled, BenchmarkRunner
from ollama_workstation.benchmarking.scoring import evaluate, wilson_interval


class FakeClient:
    def __init__(self, outputs: list[str], *, cancel_on_call: bool = False) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, Any]] = []
        self.cancel_on_call = cancel_on_call

    def version(self) -> str:
        return "0.11.2"

    def chat(self, model: str, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
        if self.cancel_on_call:
            event = kwargs.get("cancel_event")
            if event is not None:
                event.set()
            raise RuntimeError("cancelled by fake provider")
        return {
            "message": {"role": "assistant", "content": self.outputs.pop(0)},
            "eval_count": 20,
            "eval_duration": 1_000_000_000,
        }


class FakeTelemetry:
    def snapshot(self) -> dict[str, Any]:
        return {"os": "test-host", "logical_cpu_count": 8}


def test_core_suite_is_versioned_and_contains_independent_oracles() -> None:
    suite = get_suite("core")
    assert suite == CORE_SUITE
    assert suite.suite_id == "11vated-core"
    assert suite.version == "1.0.0"
    assert len(suite.cases) >= 6
    assert len({case.case_id for case in suite.cases}) == len(suite.cases)


def test_deterministic_evaluators_check_exact_numeric_json_and_order() -> None:
    exact = BenchmarkCase("exact", "instruction", "p", "exact", "OK", "exact")
    numeric = BenchmarkCase("num", "math", "p", "numeric", 323, "number")
    ordered = BenchmarkCase("order", "planning", "p", "ordered_terms",
                            ["inspect", "test", "deploy"], "order")
    json_case = next(case for case in CORE_SUITE.cases if case.evaluator == "json")

    assert evaluate(exact, "OK") == (True, "Exact output match")
    assert evaluate(numeric, "323") == (True, "Expected 323; received 323")
    assert evaluate(ordered, "First inspect, then test, finally deploy.") == (
        True, "Required terms appear in order"
    )
    assert evaluate(ordered, "test then inspect then deploy")[0] is False
    assert evaluate(json_case, '{"name":"Ada Lovelace","role":"mathematician","year":1815}')[0]
    assert not evaluate(json_case, '{"name":"Ada Lovelace","role":"mathematician","year":"1815"}')[0]


def test_wilson_interval_bounds_observed_success_rate() -> None:
    low, high = wilson_interval(8, 10)
    assert 0.0 <= low < 0.8 < high <= 1.0
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_benchmark_runner_is_repeatable_and_records_a_hashed_manifest() -> None:
    per_case_outputs = [
        "ORBIT-17", "323", "2",
        '{"name":"Ada Lovelace","role":"mathematician","year":1815}',
        "8", "1. inspect, 2. test, 3. deploy",
    ]
    outputs = [output for output in per_case_outputs for _ in range(2)]
    client = FakeClient(outputs)
    runner = BenchmarkRunner(client, telemetry=FakeTelemetry())  # type: ignore[arg-type]
    report = runner.run("fake:latest", model_digest="sha256:local", repeats=2)

    assert report.status == "completed"
    assert report.model_digest == "sha256:local"
    assert report.ollama_version == "0.11.2"
    assert report.inference_options == {"temperature": 0.0}
    assert all(call["kwargs"]["options"] == {"temperature": 0.0} for call in client.calls)
    assert report.suite_id == "11vated-core"
    assert len(report.manifest_sha256) == 64
    assert report.total_attempts == len(CORE_SUITE.cases) * 2
    assert report.total_passed == report.total_attempts
    assert report.score == 100.0
    assert all(case.score == 100.0 for case in report.cases)
    assert report.cases[0].mean_generation_tokens_per_second == 20.0
    assert report.host["os"] == "test-host"
    assert report.to_dict()["score"] == 100.0
    summary = report.profile_summary()
    assert summary["score"] == 100.0
    assert summary["suite"] == "11vated-core@1.0.0"
    assert summary["manifest_sha256"] == report.manifest_sha256
    assert summary["mean_generation_tokens_per_second"] == 20.0
    assert "instruction_following" in summary["capabilities"]


def test_benchmark_runner_redacts_output_preview_and_error_text(monkeypatch: pytest.MonkeyPatch) -> None:
    case = BenchmarkCase(
        "contains-safe", "instruction_following", "prompt", "contains",
        ["ready"], "Redaction regression",
    )
    suite = BenchmarkSuite("fixture", "1.0", "fixture", (case,))
    monkeypatch.setattr("ollama_workstation.benchmarking.runner.get_suite", lambda _name: suite)
    client = FakeClient(["ready API_TOKEN=benchmark-secret-value"])
    runner = BenchmarkRunner(client, telemetry=FakeTelemetry())  # type: ignore[arg-type]
    report = runner.run("fake:latest", repeats=1)

    assert report.cases[0].passed == 1
    assert "benchmark-secret-value" not in report.cases[0].results[0].output_preview
    assert "[REDACTED]" in report.cases[0].results[0].output_preview


def test_benchmark_cancellation_stops_the_run_without_a_partial_certificate() -> None:
    client = FakeClient([], cancel_on_call=True)
    runner = BenchmarkRunner(client, telemetry=FakeTelemetry())  # type: ignore[arg-type]
    with pytest.raises(BenchmarkCancelled, match="cancelled"):
        runner.run("fake:latest", repeats=1, cancel_event=Event())
    assert len(client.calls) == 1



def test_benchmark_manifest_changes_when_inference_parameters_change() -> None:
    first = BenchmarkRunner._manifest(
        CORE_SUITE, 3, {
            "keep_alive": "5m",
            "ollama_version": "0.11.2",
            "inference_options": {"temperature": 0.0},
        },
    )
    second = BenchmarkRunner._manifest(
        CORE_SUITE, 3, {
            "keep_alive": "5m",
            "ollama_version": "0.11.2",
            "inference_options": {"temperature": 0.2},
        },
    )
    assert first != second


def test_benchmark_runner_rejects_non_numeric_or_unknown_inference_options() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        BenchmarkRunner._normalize_inference_options({"api_token": "never-store-me"})
    with pytest.raises(TypeError, match="numeric"):
        BenchmarkRunner._normalize_inference_options({"temperature": "zero"})
    with pytest.raises(ValueError, match="finite"):
        BenchmarkRunner._normalize_inference_options({"temperature": float("nan")})
