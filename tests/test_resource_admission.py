"""Resource admission: measured RAM/VRAM decide whether a model may be loaded."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_profile
from support.scripted_ollama import ScriptedOllama

from ollama_workstation.agentification.harness import HarnessLab
from ollama_workstation.agentification.resources import (
    ADMIT,
    ADMIT_AFTER_UNLOAD,
    ADMIT_DEGRADED,
    ADMIT_UNMEASURED,
    DENY,
    FALLBACK_KV_BYTES_PER_TOKEN,
    GIB,
    MIB,
    AdmissionController,
    Footprint,
    GpuState,
    HostResources,
    ResidentModel,
    ResourceDenied,
    decide,
    estimate_footprint,
)
from ollama_workstation.cli import main


def _footprint(gib: float) -> Footprint:
    return Footprint(int(gib * GIB), 0, 4096, "fallback_constant")


def _host(ram_available: float | None = 24, vram_free: float | None = 11) -> HostResources:
    gpus = () if vram_free is None else (GpuState("gpu", 12 * GIB, int(vram_free * GIB)),)
    if ram_available is None:
        return HostResources(None, None, gpus)
    return HostResources(32 * GIB, int(ram_available * GIB), gpus)


def test_footprint_uses_model_geometry_and_grouped_query_attention() -> None:
    info = {"qwen2.block_count": 28, "qwen2.embedding_length": 3584,
            "qwen2.attention.head_count": 28, "qwen2.attention.head_count_kv": 4}
    footprint = estimate_footprint(4 * GIB, info, 8192)
    assert footprint.kv_basis == "model_geometry"
    assert footprint.kv_cache_bytes == 28 * (3584 * 4 // 28) * 4 * 8192
    assert footprint.weights_bytes == int(4 * GIB * 1.10)
    fallback = estimate_footprint(4 * GIB, None, None)
    assert fallback.kv_basis == "fallback_constant"
    assert fallback.kv_cache_bytes == FALLBACK_KV_BYTES_PER_TOKEN * 4096
    assert estimate_footprint(4 * GIB, info, 16384).total_bytes > footprint.total_bytes


def test_model_that_fits_in_free_vram_is_admitted() -> None:
    decision = decide("m", _footprint(5), _host(), [])
    assert decision.outcome == ADMIT and decision.admitted and not decision.unload_candidates


def test_resident_model_costs_nothing_more() -> None:
    resident = [ResidentModel("m", 9 * GIB, 9 * GIB)]
    decision = decide("m", _footprint(9), _host(vram_free=1), resident)
    assert decision.outcome == ADMIT and decision.required_bytes == 0


def test_load_that_needs_another_model_evicted_names_it() -> None:
    resident = [ResidentModel("small", 2 * GIB, 2 * GIB), ResidentModel("big", 8 * GIB, 8 * GIB)]
    decision = decide("m", _footprint(9), _host(ram_available=3, vram_free=1.5), resident)
    assert decision.outcome == ADMIT_AFTER_UNLOAD
    assert decision.unload_candidates == ("big", "small")


def test_model_larger_than_vram_spills_to_cpu_and_says_so() -> None:
    decision = decide("m", _footprint(18), _host(ram_available=24, vram_free=11), [])
    assert decision.outcome == ADMIT_DEGRADED
    assert "slower" in decision.reasons[0]


def test_model_that_cannot_fit_anywhere_is_denied_with_guidance() -> None:
    decision = decide("m", _footprint(40), _host(ram_available=24, vram_free=11), [])
    assert decision.outcome == DENY and not decision.admitted
    assert "smaller model" in decision.reasons[1]


def test_cpu_only_host_uses_ram_minus_the_os_reserve() -> None:
    # 32 GiB total -> 3.2 GiB reserve; 8 GiB available leaves 4.8 GiB usable.
    assert decide("m", _footprint(4.5), _host(8, None), []).outcome == ADMIT
    assert decide("m", _footprint(5), _host(8, None), []).outcome == DENY


def test_missing_measurements_are_reported_not_guessed() -> None:
    assert decide("m", _footprint(5), _host(None, None), []).outcome == ADMIT_UNMEASURED
    assert decide("m", Footprint(0, 0, 4096, "fallback_constant"), _host(), []).outcome == \
        ADMIT_UNMEASURED
    over_vram = decide("m", _footprint(18), _host(None, 11), [])
    assert over_vram.outcome == ADMIT_UNMEASURED and "could not be measured" in over_vram.reasons[0]


def test_headroom_is_kept_free_on_the_gpu() -> None:
    host = _host(ram_available=2, vram_free=5.2)  # RAM fully reserved
    assert decide("m", _footprint(5), host, []).outcome == DENY
    assert decide("m", _footprint(4.6), host, []).outcome == ADMIT


def test_snapshot_parsing_ignores_incomplete_gpu_rows() -> None:
    host = HostResources.from_snapshot({
        "system_memory": {"total_bytes": 32 * GIB, "available_bytes": 20 * GIB},
        "nvidia_gpus": [{"name": "a", "memory_total_mib": 12282, "memory_free_mib": 11000},
                        {"name": "b", "memory_total_mib": None, "memory_free_mib": None}],
    })
    assert len(host.gpus) == 1 and host.vram_free_bytes == 11000 * MIB


def _tight() -> dict:
    return {"system_memory": {"total_bytes": 4 * GIB, "available_bytes": 1 * GIB},
            "nvidia_gpus": []}


def _roomy() -> dict:
    return {"system_memory": {"total_bytes": 64 * GIB, "available_bytes": 48 * GIB},
            "nvidia_gpus": []}


def test_controller_reads_sizes_from_ollama(client) -> None:
    controller = AdmissionController(client, snapshot=_roomy)
    decision = controller.evaluate("native-pro:latest", 2048)
    assert decision.outcome == ADMIT
    assert decision.footprint is not None and decision.footprint.num_ctx == 2048
    assert decision.footprint.weights_bytes == int(1_000_000 * 1.10)
    with pytest.raises(ResourceDenied) as error:
        AdmissionController(client, snapshot=_tight).require("native-pro:latest")
    assert error.value.decision.outcome == DENY


def test_agent_run_is_refused_before_any_model_request_when_resources_are_short(
        service, client, repository, store, tmp_path: Path) -> None:
    profile = make_profile(client, repository, "native-pro:latest")
    record = HarnessLab(client).qualify("native-pro:latest", profile, trials=1, seed=3)
    store.save_qualification(record.to_dict())
    agent = service.create(name="Builder", profile=profile)
    service.activate(agent.agent_id)
    task = "Create a file named status.txt containing the text ready-ABC. Then report it."

    service.admission = AdmissionController(client, snapshot=_tight)
    with pytest.raises(ResourceDenied):
        service.run(agent.agent_id, task, tmp_path / "ws", profile)
    assert not (tmp_path / "ws" / "status.txt").exists()
    assert repository.list_run_checkpoints() == []

    events = []
    service.admission = AdmissionController(client, snapshot=_roomy)
    outcome = service.run(agent.agent_id, task, tmp_path / "ws", profile, event=events.append)
    assert outcome.result.status == "completed"
    admitted = [item for item in events if item.kind == "resource_admission"]
    assert len(admitted) == 1 and admitted[0].data["outcome"] == ADMIT


def test_resources_command_reports_every_installed_model(
        tmp_path: Path, monkeypatch, scripted: ScriptedOllama, capsys) -> None:
    import json

    monkeypatch.setenv("OLLAMA_WORKSTATION_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OLLAMA_WORKSTATION_OLLAMA_URL", scripted.base_url)
    code = main(["resources", "--num-ctx", "1024"])
    report = json.loads(capsys.readouterr().out)
    assert {item["model"] for item in report} == set(scripted.personas)
    assert all(item["footprint"]["num_ctx"] == 1024 for item in report)
    assert code == (0 if all(item["admitted"] for item in report) else 1)
