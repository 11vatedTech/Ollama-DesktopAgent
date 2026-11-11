from ollama_workstation.profiling.profiler import ModelProfiler


class FakeOllama:
    def list_models(self):
        return [{"name": "example:latest", "size": 123, "digest": "sha256:abc",
                 "details": {"family": "qwen", "parameter_size": "7B"}}]
    def show_model(self, name: str):
        assert name == "example:latest"
        return {"details": {"family": "qwen", "parameter_size": "7B", "quantization_level": "Q4_K_M"},
                "model_info": {"general.architecture": "qwen2", "qwen2.context_length": 32768},
                "capabilities": ["completion", "tools"]}

def test_metadata_only_profile_records_observed_and_unknown_facts() -> None:
    profile = ModelProfiler(FakeOllama()).profile("example:latest", run_probes=False)
    assert profile.family == "qwen"
    assert profile.architecture == "qwen2"
    assert profile.context_length == 32768
    assert profile.digest == "sha256:abc"
    assert profile.professional_title == "Unclassified Model"
    dataset = next(item for item in profile.origin_evidence if item.claim == "Training dataset disclosure")
    assert dataset.level == "unknown"
    assert all(item.status == "not_assessed" for item in profile.capabilities)

class ProbingFakeOllama(FakeOllama):
    def chat(self, model, messages, **kwargs):
        prompt = messages[-1]["content"]
        if kwargs.get("tools"):
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "probe_echo", "arguments": {"value": "agent-ready"}}}]}}
        schema = kwargs.get("output_format")
        if isinstance(schema, dict) and "status" in schema.get("properties", {}):
            content = '{"status":"ready","count":3}'
        elif isinstance(schema, dict) and "action" in schema.get("properties", {}):
            content = '{"action":"tool","name":"probe_echo","arguments":{"value":"agent-ready"}}'
        elif "VIOLET-73" in prompt:
            content = "VIOLET-73"
        elif "add_numbers" in prompt:
            content = "def add_numbers(a: int, b: int) -> int:\n    return a + b"
        elif "Read this code" in prompt:
            content = "9"
        elif "Premises:" in prompt:
            content = "NO"
        elif "Calculate" in prompt:
            content = "42"
        else:
            content = "ok"
        return {"message": {"role": "assistant", "content": content}}

def test_profiler_runs_all_v1_smoke_probes_and_records_unknowns() -> None:
    profile = ModelProfiler(ProbingFakeOllama()).profile("example:latest", run_probes=True)
    statuses = {item.capability: item.status for item in profile.capabilities}
    assert statuses["tool_calling"] == "passed"
    assert statuses["structured_actions"] == "passed"
    assert statuses["structured_output"] == "passed"
    assert statuses["code_generation"] == "passed"
    assert statuses["code_comprehension"] == "passed"
    assert statuses["reasoning"] == "passed"
    assert statuses["mathematics"] == "passed"
    assert statuses["long_context"] == "not_assessed"
    assert profile.overall_reliability is None
    assert profile.professional_title != "Unclassified Model"



def test_profile_inventory_covers_every_artifact_even_when_one_show_fails() -> None:
    class PartialInventory(FakeOllama):
        def list_models(self):
            return [
                {"name": "good:latest", "size": 123, "digest": "sha256:good",
                 "details": {"family": "qwen", "parameter_size": "7B"}},
                {"name": "broken:latest", "size": 456, "digest": "sha256:broken",
                 "details": {"family": "unknown"}},
            ]

        def show_model(self, name: str):
            if name == "broken:latest":
                raise RuntimeError("provider metadata unavailable")
            return super().show_model(name)

    profiler = ModelProfiler(PartialInventory())
    profiles = profiler.profile_inventory(run_probes=False)
    by_name = {profile.model_name: profile for profile in profiles}
    assert set(by_name) == {"good:latest", "broken:latest"}
    assert by_name["good:latest"].family == "qwen"
    assert by_name["broken:latest"].family == "unknown"
    assert by_name["broken:latest"].operational["metadata_error"] == "RuntimeError"
    assert all(
        capability.status == "not_assessed"
        for capability in by_name["broken:latest"].capabilities
    )


def test_profile_inventory_can_select_a_subset_and_reports_progress() -> None:
    messages: list[str] = []
    profiles = ModelProfiler(FakeOllama()).profile_inventory(
        names=["example:latest"], run_probes=False, progress=messages.append,
    )
    assert [profile.model_name for profile in profiles] == ["example:latest"]
    assert any("example:latest" in message for message in messages)


def test_a_failed_single_probe_is_recorded_as_failed_not_partial() -> None:
    result = ModelProfiler._result(
        "instruction_following", False, {}, "local smoke probe", "wrong token"
    )
    assert result.status == "failed"
    assert result.score == 0.0
    assert result.passed == 0


def test_probe_metrics_exclude_raw_model_message_payloads() -> None:
    class SensitiveFake(FakeOllama):
        def chat(self, model, messages, **kwargs):
            return {
                "message": {
                    "role": "assistant",
                    "content": "private raw output",
                    "tool_calls": [{
                        "function": {
                            "name": "probe_echo",
                            "arguments": {"value": "private argument"},
                        }
                    }],
                },
                "eval_count": 4,
                "eval_duration": 200_000_000,
                "prompt_eval_count": 7,
                "prompt_eval_duration": 100_000_000,
            }

    text, metrics = ModelProfiler(SensitiveFake())._ask(
        "example:latest", "probe", tools=[{"type": "function"}],
        expected_tool_call=("probe_echo", {"value": "private argument"}),
    )
    assert text == "private raw output"
    assert "message" not in metrics
    assert "private raw output" not in str(metrics)
    assert "private argument" not in str(metrics)
    assert metrics["tool_call_valid"] is True
    assert metrics["tool_call_count"] == 1
    assert metrics["generation_tokens_per_second"] == 20.0
