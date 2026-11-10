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



def test_nvidia_gpu_row_survives_unavailable_telemetry_fields(monkeypatch) -> None:
    from types import SimpleNamespace

    import ollama_workstation.profiling.telemetry as telemetry_module

    monkeypatch.setattr(telemetry_module.shutil, "which", lambda _name: "nvidia-smi")
    monkeypatch.setattr(
        telemetry_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="NVIDIA RTX Laptop, 12288, 2048, 10240, [N/A], N/A\n",
        ),
    )
    devices = SystemTelemetry._nvidia_gpus()
    assert len(devices) == 1
    assert devices[0]["name"] == "NVIDIA RTX Laptop"
    assert devices[0]["memory_total_mib"] == 12288
    assert devices[0]["memory_used_mib"] == 2048
    assert devices[0]["memory_free_mib"] == 10240
    assert devices[0]["utilization_percent"] is None
    assert devices[0]["temperature_c"] is None
