"""Local model profiling with evidence-aware capabilities and agent identity."""
from __future__ import annotations

import ast
import json
import time
from collections.abc import Callable
from typing import Any

from ollama_workstation.models.identity import assign_identity
from ollama_workstation.models.schemas import (
    CapabilityResult,
    Evidence,
    ModelProfile,
    ModelRef,
    utc_now,
)
from ollama_workstation.ollama.client import OllamaClient
from ollama_workstation.profiling.telemetry import SystemTelemetry


class ModelProfiler:
    def __init__(self, client: OllamaClient, keep_alive: str = "5m",
                 telemetry: SystemTelemetry | None = None) -> None:
        self.client = client
        self.keep_alive = keep_alive
        self.telemetry = telemetry or SystemTelemetry()

    def inventory(self) -> list[ModelRef]:
        return [ModelRef(name=str(item.get("name") or item.get("model") or "unknown"),
                         size_bytes=int(item.get("size") or 0),
                         digest=str(item.get("digest") or ""),
                         modified_at=str(item.get("modified_at") or ""),
                         details=dict(item.get("details") or {}))
                for item in self.client.list_models()]

    def profile(self, model_name: str, run_probes: bool = True,
                progress: Callable[[str], None] | None = None, repeats: int = 1) -> ModelProfile:
        notify = progress or (lambda _message: None)
        try:
            host_before = self.telemetry.snapshot()
        except Exception:
            host_before = {}
        model = next((item for item in self.inventory() if item.name == model_name), ModelRef(model_name))
        show = self.client.show_model(model_name)
        details = dict(show.get("details") or {})
        info = dict(show.get("model_info") or {})
        raw = show.get("capabilities", [])
        declared = [str(item).lower() for item in raw if isinstance(item, str)] if isinstance(raw, list) else []
        families = details.get("families")
        family = str(families[0] if isinstance(families, list) and families else details.get("family") or "unknown")
        context = next((value for key, value in info.items() if key.endswith(".context_length")), None)
        profile = ModelProfile(
            model_name=model_name, digest=model.digest, generated_at=utc_now(),
            family=str(details.get("family") or family),
            parameter_size=str(details.get("parameter_size") or info.get("general.parameter_count") or "unknown"),
            quantization=str(details.get("quantization_level") or "unknown"),
            architecture=str(info.get("general.architecture") or family),
            context_length=int(context) if isinstance(context, (int, float)) else None,
            declared_capabilities=declared,
            declared_purpose=self._declared_purpose(model_name, family),
            origin_evidence=[
                Evidence("Installed model", model_name, "observed", "Ollama /api/tags and /api/show",
                         "https://github.com/ollama/ollama/blob/main/docs/api.md"),
                Evidence("Model family", family, "observed", "Ollama /api/show"),
                Evidence("Parameter size", str(details.get("parameter_size") or "not disclosed"),
                         "observed", "Ollama /api/show"),
                Evidence("Quantization", str(details.get("quantization_level") or "not disclosed"),
                         "observed", "Ollama /api/show"),
                Evidence("Architecture", str(info.get("general.architecture") or family),
                         "observed", "Ollama model_info"),
                Evidence("Declared capabilities", ", ".join(declared) or "not reported",
                         "observed", "Ollama /api/show"),
                Evidence("Training dataset disclosure", "Not available from local Ollama metadata",
                         "unknown", "Primary model card or technical report required",
                         notes="Never infer training data from a model name."),
                Evidence("Ollama artifact digest", model.digest or "not available",
                         "observed", "Ollama /api/tags"),
            ],
            operational={"inventory_size_bytes": model.size_bytes, "modified_at": model.modified_at,
                         "ollama_digest": model.digest, "probe_profile": "local_low_cost_v1"},
        )
        if run_probes:
            probes = [
                ("instruction_following", lambda: self._probe_instruction(model_name)),
                ("structured_output", lambda: self._probe_structured(model_name)),
                ("structured_actions", lambda: self._probe_structured_actions(model_name)),
                ("tool_calling", lambda: self._probe_tools(model_name)),
                ("code_generation", lambda: self._probe_code(model_name)),
                ("code_comprehension", lambda: self._probe_code_comprehension(model_name)),
                ("reasoning", lambda: self._probe_reasoning(model_name)),
                ("mathematics", lambda: self._probe_math(model_name)),
            ]
            repeat_count = max(1, min(int(repeats), 5))
            profile.operational["probe_repeats"] = repeat_count
            for name, probe in probes:
                outcomes: list[CapabilityResult] = []
                for attempt in range(repeat_count):
                    notify(f"Testing {name.replace('_', ' ')} (attempt {attempt + 1}/{repeat_count})…")
                    try:
                        outcomes.append(probe())
                    except Exception as exc:
                        outcomes.append(CapabilityResult(
                            name, "failed", 0.0, attempts=1, passed=0,
                            method="local smoke probe v1",
                            limitations=[type(exc).__name__ + ": " + str(exc)]))
                profile.capabilities.append(self._aggregate(name, outcomes))
            for name in ("long_context", "research", "creative_writing", "multilingual",
                         "data_extraction", "planning", "error_recovery", "audio"):
                profile.capabilities.append(CapabilityResult(
                    name, "not_assessed", method="Dedicated task suite not included in v0.1"))
            profile.capabilities.append(CapabilityResult(
                "vision", "not_assessed" if "vision" in declared else "unsupported",
                method="Ollama capability metadata",
                evidence=["Vision is declared by Ollama."] if "vision" in declared else [],
                limitations=["Visual task quality is not empirically tested."]))
        else:
            names = ("instruction_following", "structured_output", "structured_actions", "tool_calling",
                     "code_generation", "code_comprehension", "reasoning", "mathematics", "long_context",
                     "research", "creative_writing", "multilingual", "data_extraction", "planning",
                     "error_recovery", "vision", "audio")
            profile.capabilities = [CapabilityResult(name, "not_assessed", method="Not run") for name in names]
        profile.known_limitations = [limitation for item in profile.capabilities
                                     for limitation in item.limitations]
        profile.overall_reliability = None
        try:
            profile.operational["host_before"] = host_before
            profile.operational["host_after"] = self.telemetry.snapshot()
        except Exception as exc:
            profile.operational["host_telemetry_error"] = type(exc).__name__
        try:
            running = self.client.running_models()
            profile.operational["loaded_model"] = next(
                (item for item in running
                 if str(item.get("name") or item.get("model") or "") == model_name),
                None,
            )
        except Exception:
            profile.operational["loaded_model"] = None
        profile.operational["performance_summary"] = self._performance_summary(profile.capabilities)
        assign_identity(profile)
        return profile

    @staticmethod
    def _declared_purpose(name: str, family: str) -> str:
        value = (name + " " + family).lower()
        if any(part in value for part in ("coder", "code", "devstral", "starcoder")):
            return "Coding-oriented candidate; publisher purpose must be verified."
        if "embed" in value:
            return "Embedding model candidate; not a conversational agent by default."
        if any(part in value for part in ("vision", "llava", "vl")):
            return "Vision-language model candidate; visual quality remains untested."
        if any(part in value for part in ("reason", "r1", "thinking", "qwq")):
            return "Reasoning-oriented candidate; empirical qualification required."
        return "General-purpose or undisclosed purpose; publisher research is required."

    def _ask(self, model: str, prompt: str, output_format: dict[str, Any] | None = None,
             tools: list[dict[str, Any]] | None = None,
             expected_tool_call: tuple[str, dict[str, Any]] | None = None) -> tuple[str, dict[str, Any]]:
        started = time.perf_counter()
        response = self.client.chat(model, [{"role": "user", "content": prompt}],
                                    output_format=output_format, tools=tools,
                                    keep_alive=self.keep_alive)
        message = response.get("message") or {}
        metrics: dict[str, Any] = {
            "duration_ms": (time.perf_counter() - started) * 1000,
            "eval_count": response.get("eval_count"),
            "eval_duration_ns": response.get("eval_duration"),
            "load_duration_ns": response.get("load_duration"),
            "prompt_eval_count": response.get("prompt_eval_count"),
            "prompt_eval_duration_ns": response.get("prompt_eval_duration"),
            "generation_tokens_per_second": (
                float(response["eval_count"]) / (float(response["eval_duration"]) / 1_000_000_000)
                if response.get("eval_count") and response.get("eval_duration") else None
            ),
        }
        if tools is not None:
            calls = message.get("tool_calls") or []
            metrics["tool_call_count"] = len(calls)
            if expected_tool_call is not None:
                expected_name, expected_arguments = expected_tool_call
                metrics["tool_call_valid"] = any(
                    self._matches_tool_call(call, expected_name, expected_arguments)
                    for call in calls
                )
        return str(message.get("content") or ""), metrics

    @staticmethod
    def _matches_tool_call(
        call: dict[str, Any], expected_name: str, expected_arguments: dict[str, Any]
    ) -> bool:
        """Validate one expected native tool call without retaining its raw payload."""
        function = call.get("function") or {}
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return False
        return function.get("name") == expected_name and arguments == expected_arguments

    @staticmethod
    def _result(name: str, passed: bool, metrics: dict[str, Any],
                method: str, evidence: str) -> CapabilityResult:
        return CapabilityResult(name, "passed" if passed else "partial",
            100.0 if passed else 0.0, 0.2, 1, int(passed), metrics.get("duration_ms"),
            method, [evidence],
            [] if passed else ["Single-item smoke test failed; broad capability remains unestablished."],
            metrics=metrics)

    @staticmethod
    def _aggregate(name: str, outcomes: list[CapabilityResult]) -> CapabilityResult:
        """Aggregate repeated smoke probes without promoting them to certification."""
        if not outcomes:
            return CapabilityResult(name, "not_assessed", method="No probe outcomes")
        if all(item.status == "unsupported" for item in outcomes):
            return CapabilityResult(
                name, "unsupported", confidence=0.0, attempts=len(outcomes),
                method="Repeated local smoke probe",
                limitations=[note for item in outcomes for note in item.limitations],
            )
        passed = sum(item.status == "passed" for item in outcomes)
        attempts = len(outcomes)
        has_partial = any(item.status == "partial" for item in outcomes)
        status = "passed" if passed == attempts else "partial" if passed or has_partial else "failed"
        scored = [item.score for item in outcomes if item.score is not None]
        score = (sum(scored) / len(scored)) if scored else None
        durations = [item.duration_ms for item in outcomes if item.duration_ms is not None]
        evidence = [f"Trial {index + 1}: " + "; ".join(item.evidence)
                    for index, item in enumerate(outcomes) if item.evidence]
        limitations = list(dict.fromkeys(
            note for item in outcomes for note in item.limitations
        ))
        if passed < attempts:
            limitations.append(
                f"Passed {passed}/{attempts} repeated smoke probes; broader qualification is not established."
            )
        return CapabilityResult(
            name, status, score,
            confidence=min(0.8, 0.2 + 0.15 * (attempts - 1)),
            attempts=attempts, passed=passed,
            duration_ms=(sum(durations) / len(durations)) if durations else None,
            method=f"Repeated local smoke probe ({attempts} trial(s))",
            evidence=evidence, limitations=list(dict.fromkeys(limitations)),
            metrics=ModelProfiler._aggregate_metrics(outcomes),
        )

    @staticmethod
    def _aggregate_metrics(outcomes: list[CapabilityResult]) -> dict[str, Any]:
        keys = {key for item in outcomes for key in item.metrics}
        aggregate: dict[str, Any] = {"trials": [item.metrics for item in outcomes if item.metrics]}
        for key in keys:
            values = [
                value for item in outcomes
                if isinstance((value := item.metrics.get(key)), (int, float))
                and not isinstance(value, bool)
            ]
            if values:
                aggregate[f"mean_{key}"] = sum(values) / len(values)
        return aggregate

    @staticmethod
    def _performance_summary(capabilities: list[CapabilityResult]) -> dict[str, Any]:
        metrics = [item.metrics for item in capabilities if item.metrics]
        latencies = [
            item.duration_ms for item in capabilities
            if item.duration_ms is not None
        ]
        speeds = [
            item["mean_generation_tokens_per_second"]
            for item in metrics if isinstance(item.get("mean_generation_tokens_per_second"), (int, float))
        ]
        return {
            "probe_count": sum(item.attempts for item in capabilities),
            "mean_probe_latency_ms": sum(latencies) / len(latencies) if latencies else None,
            "mean_generation_tokens_per_second": sum(speeds) / len(speeds) if speeds else None,
        }

    def _probe_instruction(self, model: str) -> CapabilityResult:
        text, metrics = self._ask(model, "Return exactly this token and nothing else: VIOLET-73.")
        return self._result("instruction_following", text.strip() == "VIOLET-73",
                            metrics, "Exact-output smoke test", text[:160])

    def _probe_structured(self, model: str) -> CapabilityResult:
        schema = {"type": "object", "properties": {
            "status": {"type": "string", "enum": ["ready"]}, "count": {"type": "integer"}},
            "required": ["status", "count"], "additionalProperties": False}
        text, metrics = self._ask(model, "Return a JSON object with status ready and count 3.", schema)
        try:
            passed = json.loads(text) == {"status": "ready", "count": 3}
        except json.JSONDecodeError:
            passed = False
        return self._result("structured_output", passed, metrics, "Ollama JSON-schema probe", text[:240])

    def _probe_structured_actions(self, model: str) -> CapabilityResult:
        schema = {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["tool"]},
            "name": {"type": "string", "enum": ["probe_echo"]},
            "arguments": {"type": "object", "properties": {
                "value": {"type": "string", "const": "agent-ready"}},
                "required": ["value"], "additionalProperties": False}},
            "required": ["action", "name", "arguments"], "additionalProperties": False}
        text, metrics = self._ask(model, "Return tool action probe_echo with value agent-ready.", schema)
        try:
            value = json.loads(text)
            passed = (value.get("action") == "tool" and value.get("name") == "probe_echo"
                      and value.get("arguments", {}).get("value") == "agent-ready")
        except (json.JSONDecodeError, AttributeError):
            passed = False
        return self._result("structured_actions", passed, metrics, "Structured action schema probe", text[:240])

    def _probe_tools(self, model: str) -> CapabilityResult:
        schema = [{"type": "function", "function": {
            "name": "probe_echo", "description": "Harmless function-call test.",
            "parameters": {"type": "object", "properties": {"value": {"type": "string"}},
                           "required": ["value"], "additionalProperties": False}}}]
        _, metrics = self._ask(
            model, "Call probe_echo exactly once with value agent-ready.",
            tools=schema, expected_tool_call=("probe_echo", {"value": "agent-ready"}),
        )
        call_count = int(metrics.get("tool_call_count", 0))
        valid = bool(metrics.get("tool_call_valid", False))
        result = self._result("tool_calling", valid, metrics, "Native function-call probe",
                              "Tool calls returned: " + str(call_count))
        if not call_count:
            result.status, result.score = "unsupported", None
            result.limitations.append("No native tool call; test alternative action harnesses.")
        elif not valid:
            result.status, result.score = "partial", 40.0
            result.limitations.append("Tool call had unexpected function or arguments.")
        return result

    def _probe_code(self, model: str) -> CapabilityResult:
        text, metrics = self._ask(model, "Write only Python code defining def add_numbers(a: int, b: int) -> int: that returns a + b. No markdown.")
        try:
            tree = ast.parse(text.strip())
            function = next((node for node in tree.body if isinstance(node, ast.FunctionDef)
                             and node.name == "add_numbers"), None)
            returned = next((node.value for node in ast.walk(function) if isinstance(node, ast.Return)), None) if function else None
            expected = ast.BinOp(ast.Name("a", ast.Load()), ast.Add(), ast.Name("b", ast.Load()))
            passed = bool(function and returned and ast.dump(returned) == ast.dump(expected))
        except SyntaxError:
            passed = False
        return self._result("code_generation", passed, metrics, "Python AST structure probe",
                            "Generated code parsed; execution is deliberately not performed.")

    def _probe_code_comprehension(self, model: str) -> CapabilityResult:
        prompt = ("Read this code: def total(items):\n    subtotal = sum(items)\n    return subtotal + 2\n\n"
                  "For input [3, 4], what exact integer is returned? Reply with the integer only.")
        text, metrics = self._ask(model, prompt)
        passed = text.strip().strip(". ") == "9"
        return self._result("code_comprehension", passed, metrics, "Deterministic code-tracing probe", text[:120])

    def _probe_reasoning(self, model: str) -> CapabilityResult:
        prompt = ("Premises: Every neral is a vesk. No vesk is transparent. "
                  "Can any neral be transparent? Answer exactly YES or NO.")
        text, metrics = self._ask(model, prompt)
        passed = text.strip().upper().strip(".! ") == "NO"
        return self._result("reasoning", passed, metrics, "Single syllogism smoke test", text[:120])

    def _probe_math(self, model: str) -> CapabilityResult:
        text, metrics = self._ask(model, "Calculate (18 * 3) - 12. Reply with the integer only.")
        passed = text.strip().strip(". ") == "42"
        return self._result("mathematics", passed, metrics, "Deterministic arithmetic smoke test", text[:120])
