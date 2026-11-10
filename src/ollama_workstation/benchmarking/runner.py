"""Local deterministic benchmark runner."""

from __future__ import annotations

import hashlib
import json
import statistics
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from threading import Event
from typing import Any

from ollama_workstation.agents.tools import WorkspaceTools
from ollama_workstation.benchmarking.catalog import get_suite
from ollama_workstation.benchmarking.models import (
    BenchmarkAttempt,
    BenchmarkCaseResult,
    BenchmarkRun,
)
from ollama_workstation.benchmarking.scoring import evaluate, wilson_interval
from ollama_workstation.ollama.client import OllamaClient
from ollama_workstation.profiling.telemetry import SystemTelemetry


class BenchmarkCancelled(RuntimeError):
    """Raised when a benchmark is cancelled before a complete report exists."""


class BenchmarkRunner:
    """Runs fixed prompts repeatedly and scores results with host-side deterministic oracles."""

    def __init__(
        self,
        client: OllamaClient,
        *,
        keep_alive: str = "5m",
        telemetry: SystemTelemetry | None = None,
    ) -> None:
        self.client = client
        self.keep_alive = keep_alive
        self.telemetry = telemetry or SystemTelemetry()

    @staticmethod
    def _manifest(suite: Any, repeats: int, options: dict[str, Any] | None) -> str:
        payload = {
            "suite_id": suite.suite_id,
            "suite_version": suite.version,
            "repeats": repeats,
            "options": options or {},
            "cases": [
                {
                    "case_id": case.case_id,
                    "capability": case.capability,
                    "prompt": case.prompt,
                    "evaluator": case.evaluator,
                    "expected": case.expected,
                    "schema": case.output_schema,
                }
                for case in suite.cases
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def run(
        self,
        model_name: str,
        *,
        model_digest: str = "",
        suite_id: str = "core",
        repeats: int = 3,
        cancel_event: Event | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> BenchmarkRun:
        repeats = max(1, min(int(repeats), 5))
        suite = get_suite(suite_id)
        notify = progress or (lambda _message: None)
        started_at = datetime.now(UTC).isoformat()
        manifest = self._manifest(suite, repeats, {"keep_alive": self.keep_alive})
        results: list[BenchmarkCaseResult] = []

        for case in suite.cases:
            attempts: list[BenchmarkAttempt] = []
            for trial in range(1, repeats + 1):
                if cancel_event is not None and cancel_event.is_set():
                    raise BenchmarkCancelled("Benchmark cancelled; partial results were not certified.")
                notify(f"{case.case_id}: trial {trial}/{repeats}")
                started = time.perf_counter()
                try:
                    response = self.client.chat(
                        model_name,
                        [{"role": "user", "content": case.prompt}],
                        output_format=case.output_schema if case.evaluator == "json" else None,
                        keep_alive=self.keep_alive,
                        cancel_event=cancel_event,
                    )
                    if cancel_event is not None and cancel_event.is_set():
                        raise BenchmarkCancelled("Benchmark cancelled during an Ollama request.")
                    message = response.get("message") or {}
                    raw_output = str(message.get("content") or "")
                    safe_output, _ = WorkspaceTools.redact_sensitive_text(raw_output)
                    passed, _ = evaluate(case, raw_output)
                    elapsed = (time.perf_counter() - started) * 1000.0
                    eval_count = response.get("eval_count")
                    eval_duration = response.get("eval_duration")
                    try:
                        numeric_eval_count = float(eval_count)
                        numeric_eval_duration = float(eval_duration)
                    except (TypeError, ValueError):
                        numeric_eval_count = 0.0
                        numeric_eval_duration = 0.0
                    speed = (
                        numeric_eval_count / (numeric_eval_duration / 1_000_000_000)
                        if numeric_eval_count > 0 and numeric_eval_duration > 0 else None
                    )
                    attempts.append(BenchmarkAttempt(
                        attempt=trial,
                        passed=passed,
                        duration_ms=elapsed,
                        output_preview=safe_output[:2_000],
                        eval_count=int(eval_count) if eval_count is not None else None,
                        generation_tokens_per_second=speed,
                    ))
                except BenchmarkCancelled:
                    raise
                except Exception as exc:
                    if cancel_event is not None and cancel_event.is_set():
                        raise BenchmarkCancelled("Benchmark cancelled during an Ollama request.") from exc
                    elapsed = (time.perf_counter() - started) * 1000.0
                    safe_error, _ = WorkspaceTools.redact_sensitive_text(str(exc)[:500])
                    attempts.append(BenchmarkAttempt(
                        attempt=trial,
                        passed=False,
                        duration_ms=elapsed,
                        output_preview="",
                        error=f"{type(exc).__name__}: {safe_error}",
                    ))

            passed_count = sum(item.passed for item in attempts)
            low, high = wilson_interval(passed_count, len(attempts))
            latencies = [item.duration_ms for item in attempts]
            sorted_latencies = sorted(latencies)
            p95_index = max(0, min(len(sorted_latencies) - 1, int(0.95 * len(sorted_latencies) + 0.999) - 1))
            speeds = [
                item.generation_tokens_per_second for item in attempts
                if item.generation_tokens_per_second is not None
            ]
            results.append(BenchmarkCaseResult(
                case_id=case.case_id,
                capability=case.capability,
                evaluator=case.evaluator,
                attempts=len(attempts),
                passed=passed_count,
                score=passed_count / len(attempts) * 100.0 if attempts else 0.0,
                wilson_95_low=low,
                wilson_95_high=high,
                median_latency_ms=statistics.median(latencies) if latencies else None,
                p95_latency_ms=sorted_latencies[p95_index] if sorted_latencies else None,
                mean_generation_tokens_per_second=statistics.mean(speeds) if speeds else None,
                results=attempts,
            ))

        try:
            host = self.telemetry.snapshot()
        except Exception as exc:
            host = {"telemetry_error": type(exc).__name__}
        return BenchmarkRun(
            run_id=str(uuid.uuid4()),
            suite_id=suite.suite_id,
            suite_version=suite.version,
            model_name=model_name,
            model_digest=model_digest,
            repeats=repeats,
            manifest_sha256=manifest,
            started_at=started_at,
            completed_at=datetime.now(UTC).isoformat(),
            host=host,
            cases=results,
        )
