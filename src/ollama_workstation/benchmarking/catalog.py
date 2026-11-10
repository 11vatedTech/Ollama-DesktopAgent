"""Versioned, deterministic benchmark suites with independent host-side scoring."""

from __future__ import annotations

from ollama_workstation.benchmarking.models import BenchmarkCase, BenchmarkSuite

CORE_SUITE = BenchmarkSuite(
    suite_id="11vated-core",
    version="1.0.0",
    description=(
        "A deterministic local smoke benchmark for instruction following, arithmetic, "
        "reasoning, structured extraction, code comprehension and planning. "
        "It is not equivalent to SWE-bench, BFCL, OSWorld or a broad model leaderboard."
    ),
    cases=(
        BenchmarkCase(
            case_id="instruction-exact-01",
            capability="instruction_following",
            description="Exact-output compliance with an explicit single-token contract.",
            prompt="Return exactly this string and nothing else: ORBIT-17",
            evaluator="exact",
            expected="ORBIT-17",
        ),
        BenchmarkCase(
            case_id="arithmetic-multiply-01",
            capability="mathematics",
            description="Small exact arithmetic item with a numeric oracle.",
            prompt="Calculate 17 multiplied by 19. Return only the number.",
            evaluator="numeric",
            expected=323,
        ),
        BenchmarkCase(
            case_id="reasoning-loop-01",
            capability="reasoning",
            description="Deterministic tracing of a short program without execution.",
            prompt=(
                "Trace this Python function for n=4. Return only the final integer.\n"
                "total = 0\n"
                "for i in range(1, n + 1):\n"
                "    if i % 2:\n"
                "        total += i * 2\n"
                "    else:\n"
                "        total -= i\n"
                "print(total)"
            ),
            evaluator="numeric",
            expected=2,
        ),
        BenchmarkCase(
            case_id="structured-extraction-01",
            capability="structured_output",
            description="Extract fields from a fixed synthetic record into strict JSON.",
            prompt=(
                "Extract the record into JSON with exactly these fields: name, role, year. "
                "Do not add prose. Record: Name=Ada Lovelace; Role=mathematician; Year=1815."
            ),
            evaluator="json",
            expected={"name": "Ada Lovelace", "role": "mathematician", "year": 1815},
            output_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "year": {"type": "integer"},
                },
                "required": ["name", "role", "year"],
                "additionalProperties": False,
            },
        ),
        BenchmarkCase(
            case_id="code-comprehension-01",
            capability="code_comprehension",
            description="Trace a deterministic branch and loop without executing code.",
            prompt=(
                "What integer is printed? Return only the number.\n"
                "def calculate():\n"
                "    values = [3, 5, 8, 2]\n"
                "    result = 0\n"
                "    for value in values:\n"
                "        if value > 4:\n"
                "            result += value\n"
                "        else:\n"
                "            result -= value\n"
                "    return result\n"
                "print(calculate())"
            ),
            evaluator="numeric",
            expected=8,
        ),
        BenchmarkCase(
            case_id="planning-order-01",
            capability="planning",
            description="Produce a short process that preserves required dependency order.",
            prompt=(
                "Write a concise three-step plan for safely updating a software dependency. "
                "Your answer must mention these three terms in this exact order: "
                "inspect, test, deploy."
            ),
            evaluator="ordered_terms",
            expected=["inspect", "test", "deploy"],
        ),
    ),
)


def get_suite(suite_id: str = "core") -> BenchmarkSuite:
    normalized = suite_id.casefold()
    if normalized in {"core", "11vated-core"}:
        return CORE_SUITE
    raise ValueError(f"Unknown benchmark suite: {suite_id}")
