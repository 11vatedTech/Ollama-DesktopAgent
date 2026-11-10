from ollama_workstation.profiling.telemetry import SystemTelemetry


def test_system_telemetry_returns_portable_host_snapshot() -> None:
    snapshot = SystemTelemetry().snapshot()
    assert snapshot["captured_at"]
    assert snapshot["os"]
    assert snapshot["machine"]
    assert isinstance(snapshot["logical_cpu_count"], int) or snapshot["logical_cpu_count"] is None
    assert set(snapshot["system_memory"]) == {"total_bytes", "available_bytes", "used_bytes"}
    assert isinstance(snapshot["nvidia_gpus"], list)


def test_memory_snapshot_never_returns_negative_values() -> None:
    memory = SystemTelemetry._memory()
    for value in memory.values():
        assert value is None or value >= 0
