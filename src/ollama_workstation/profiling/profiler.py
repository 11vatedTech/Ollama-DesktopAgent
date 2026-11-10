"""Ollama model inventory and evidence-aware smoke profiling."""
from __future__ import annotations
import time
from typing import Any, Callable
from ollama_workstation.models.identity import assign_identity
from ollama_workstation.models.schemas import CapabilityResult, Evidence, ModelProfile, ModelRef, utc_now
from ollama_workstation.ollama.client import OllamaClient

class ModelProfiler:
    def __init__(self, client: OllamaClient, keep_alive: str = "5m") -> None:
        self.client = client
        self.keep_alive = keep_alive

    def inventory(self) -> list[ModelRef]:
        return [ModelRef(name=str(x.get("name") or x.get("model") or "unknown"),
                         size_bytes=int(x.get("size") or 0), digest=str(x.get("digest") or ""),
                         modified_at=str(x.get("modified_at") or ""), details=dict(x.get("details") or {}))
                for x in self.client.list_models()]

    def profile(self, model_name: str, run_probes: bool = True,
                progress: Callable[[str], None] | None = None) -> ModelProfile:
        notify = progress or (lambda message: None)
        model = next((x for x in self.inventory() if x.name == model_name), ModelRef(model_name))
        show = self.client.show_model(model_name)
        details = dict(show.get("details") or {})
        info = dict(show.get("model_info") or {})
        family = str(details.get("family") or "unknown")
        profile = ModelProfile(
            model_name=model_name, digest=model.digest, generated_at=utc_now(),
            family=family, parameter_size=str(details.get("parameter_size") or "unknown"),
            quantization=str(details.get("quantization_level") or "unknown"),
            architecture=str(info.get("general.architecture") or family),
            context_length=next((int(v) for k, v in info.items() if k.endswith(".context_length")
                                 and isinstance(v, (int, float))), None),
            declared_capabilities=[str(x) for x in show.get("capabilities", [])],
            declared_purpose="Publisher purpose not verified; attach a primary model card.",
            origin_evidence=[
                Evidence("Installed model", model_name, "observed", "Ollama /api/tags"),
                Evidence("Model family", family, "observed", "Ollama /api/show"),
                Evidence("Quantization", str(details.get("quantization_level") or "unknown"),
                         "observed", "Ollama /api/show"),
                Evidence("Training datasets", "Unknown from local metadata", "unknown",
                         notes="Do not infer training data from model name."),
            ],
            operational={"inventory_size_bytes": model.size_bytes, "ollama_digest": model.digest},
        )
        if run_probes:
            for label, prompt, expected in (
                ("instruction_following", "Return exactly VIOLET-73.", "VIOLET-73"),
                ("reasoning", "All neral are vesk. No vesk are transparent. Answer YES or NO: can a neral be transparent?", "NO"),
                ("mathematics", "Calculate 18 times 3 minus 12. Return integer only.", "42"),
            ):
                notify("Testing " + label)
                started = time.perf_counter()
                try:
                    response = self.client.chat(model_name, [{"role": "user", "content": prompt}],
                                                keep_alive=self.keep_alive)
                    answer = str((response.get("message") or {}).get("content") or "").strip()
                    passed = answer.upper().strip(".! ") == expected
                    profile.capabilities.append(CapabilityResult(
                        label, "passed" if passed else "partial", 100.0 if passed else 0.0,
                        0.2, 1, int(passed), (time.perf_counter() - started) * 1000,
                        "Single-item local smoke test", [answer[:160]],
                        [] if passed else ["Single-item test failed; capability remains unestablished."]))
                except Exception as exc:
                    profile.capabilities.append(CapabilityResult(
                        label, "failed", method="Local smoke test",
                        limitations=[type(exc).__name__ + ": " + str(exc)]))
            for label in ("structured_output", "structured_actions", "tool_calling", "code_generation",
                          "code_comprehension", "long_context", "research", "creative_writing",
                          "multilingual", "data_extraction", "planning", "error_recovery", "vision", "audio"):
                profile.capabilities.append(CapabilityResult(
                    label, "not_assessed", method="Dedicated benchmark not included in this quick profile"))
        else:
            for label in ("instruction_following", "reasoning", "mathematics", "tool_calling",
                          "code_generation", "vision", "long_context"):
                profile.capabilities.append(CapabilityResult(label, "not_assessed", method="Not run"))
        profile.known_limitations = [lim for item in profile.capabilities for lim in item.limitations]
        profile.overall_reliability = None
        assign_identity(profile)
        return profile
