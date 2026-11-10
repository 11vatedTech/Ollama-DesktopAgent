"""Independent deterministic evaluators and confidence intervals."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from ollama_workstation.benchmarking.models import BenchmarkCase


def wilson_interval(passed: int, attempts: int, z: float = 1.96) -> tuple[float, float]:
    if attempts <= 0:
        return 0.0, 0.0
    proportion = passed / attempts
    denominator = 1.0 + z * z / attempts
    centre = (proportion + z * z / (2 * attempts)) / denominator
    margin = (
        z * math.sqrt(
            proportion * (1.0 - proportion) / attempts
            + z * z / (4 * attempts * attempts)
        ) / denominator
    )
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _extract_json(text: str) -> Any:
    candidate = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.I | re.S)
    if fenced:
        candidate = fenced.group(1).strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end < start:
        raise ValueError("No JSON object found")
    return json.loads(candidate[start:end + 1])


def _matches_schema(value: Any, schema: dict[str, Any]) -> bool:
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(value, dict):
            return False
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if any(key not in value for key in required):
            return False
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            return False
        return all(
            _matches_schema(value[key], child_schema)
            for key, child_schema in properties.items() if key in value
        )
    if expected_type == "array":
        return isinstance(value, list) and all(
            _matches_schema(item, schema.get("items", {})) for item in value
        )
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return True


def evaluate(case: BenchmarkCase, output: str) -> tuple[bool, str]:
    text = output.strip()
    try:
        if case.evaluator == "exact":
            passed = text == str(case.expected)
            return passed, "Exact output match" if passed else f"Expected {case.expected!r}"

        if case.evaluator == "contains":
            terms = case.expected if isinstance(case.expected, list) else [case.expected]
            missing = [str(term) for term in terms if str(term).casefold() not in text.casefold()]
            return not missing, "All required terms found" if not missing else "Missing: " + ", ".join(missing)

        if case.evaluator == "ordered_terms":
            position = -1
            for term in case.expected:
                found = text.casefold().find(str(term).casefold(), position + 1)
                if found < 0:
                    return False, f"Required term missing or out of order: {term}"
                position = found
            return True, "Required terms appear in order"

        if case.evaluator == "numeric":
            expected = float(case.expected)
            values = re.findall(r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", text)
            if not values:
                return False, "No numeric answer found"
            actual = float(values[-1].replace(",", ""))
            passed = math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)
            return passed, f"Expected {expected:g}; received {actual:g}"

        if case.evaluator == "json":
            value = _extract_json(text)
            if case.output_schema and not _matches_schema(value, case.output_schema):
                return False, "Output did not satisfy the benchmark JSON schema"
            passed = value == case.expected
            return passed, "Exact JSON object match" if passed else "JSON object differs from the expected record"

    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return False, f"Evaluator error: {type(exc).__name__}: {exc}"
    return False, f"Unsupported evaluator: {case.evaluator}"
