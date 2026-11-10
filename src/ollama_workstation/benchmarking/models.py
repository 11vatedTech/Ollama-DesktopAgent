"""Typed models for reproducible local benchmark runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Evaluator = Literal["exact", "numeric", "contains", "ordered_terms", "json"]


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    case_id: str
    capability: str
    prompt: str
    evaluator: Evaluator
    expected: Any
    description: str
    output_schema: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkSuite:
    suite_id: str
    version: str
    description: str
    cases: tuple[BenchmarkCase, ...]


@dataclass(slots=True)
class BenchmarkAttempt:
    attempt: int
    passed: bool
    duration_ms: float
    output_preview: str
    eval_count: int | None = None
    generation_tokens_per_second: float | None = None
    error: str = ""


@dataclass(slots=True)
class BenchmarkCaseResult:
    case_id: str
    capability: str
    evaluator: str
    attempts: int
    passed: int
    score: float
    wilson_95_low: float
    wilson_95_high: float
    median_latency_ms: float | None
    p95_latency_ms: float | None
    mean_generation_tokens_per_second: float | None
    results: list[BenchmarkAttempt] = field(default_factory=list)


@dataclass(slots=True)
class BenchmarkRun:
    run_id: str
    suite_id: str
    suite_version: str
    model_name: str
    model_digest: str
    repeats: int
    manifest_sha256: str
    started_at: str
    completed_at: str
    host: dict[str, Any]
    cases: list[BenchmarkCaseResult]
    status: str = "completed"

    @property
    def total_attempts(self) -> int:
        return sum(item.attempts for item in self.cases)

    @property
    def total_passed(self) -> int:
        return sum(item.passed for item in self.cases)

    @property
    def score(self) -> float:
        return (
            self.total_passed / self.total_attempts * 100.0
            if self.total_attempts else 0.0
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["total_attempts"] = self.total_attempts
        payload["total_passed"] = self.total_passed
        payload["score"] = self.score
        return payload
