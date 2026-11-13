"""Resource admission control in front of model loads.

The host decides whether a model may be loaded for a run *before* the run
starts, from measured free RAM/VRAM, what Ollama already has resident and an
explicit estimate of the model's footprint. Nothing here is hardware-specific:
every number comes from the live snapshot.

The footprint is an ESTIMATE (weights + key/value cache for the requested
context). Ollama's own allocator has the final word; the purpose of this layer
is to refuse loads that clearly cannot fit and to tell the operator when a load
will evict another model or spill to the CPU, instead of letting the machine
thrash.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from ollama_workstation.profiling.telemetry import SystemTelemetry

MIB = 1024 * 1024
GIB = 1024 * MIB

# Engineering constants, not derived truths. Change them only with tests.
WEIGHT_OVERHEAD = 1.10            # runtime graph/scratch buffers on top of the weight file
FALLBACK_KV_BYTES_PER_TOKEN = 256 * 1024   # used when model metadata lacks layer geometry
DEFAULT_NUM_CTX = 4096
VRAM_HEADROOM_BYTES = 512 * MIB   # left free for the desktop compositor and other apps
RAM_RESERVE_FRACTION = 0.10       # of total RAM kept for the OS and this application
RAM_RESERVE_MIN_BYTES = 2 * GIB

ADMIT = "admit"
ADMIT_AFTER_UNLOAD = "admit_after_unload"
ADMIT_DEGRADED = "admit_degraded"
ADMIT_UNMEASURED = "admit_unmeasured"
DENY = "deny"
ADMITTING = frozenset({ADMIT, ADMIT_AFTER_UNLOAD, ADMIT_DEGRADED, ADMIT_UNMEASURED})


@dataclass(frozen=True, slots=True)
class GpuState:
    name: str
    total_bytes: int
    free_bytes: int


@dataclass(frozen=True, slots=True)
class ResidentModel:
    name: str
    size_bytes: int
    size_vram_bytes: int


@dataclass(frozen=True, slots=True)
class HostResources:
    ram_total_bytes: int | None
    ram_available_bytes: int | None
    gpus: tuple[GpuState, ...] = ()

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> HostResources:
        memory = snapshot.get("system_memory") or {}
        gpus: list[GpuState] = []
        for item in snapshot.get("nvidia_gpus") or []:
            total, free = item.get("memory_total_mib"), item.get("memory_free_mib")
            if isinstance(total, int) and isinstance(free, int):
                gpus.append(GpuState(str(item.get("name") or "gpu"), total * MIB, free * MIB))
        total_ram, available = memory.get("total_bytes"), memory.get("available_bytes")
        return cls(
            total_ram if isinstance(total_ram, int) else None,
            available if isinstance(available, int) else None,
            tuple(gpus),
        )

    @property
    def vram_free_bytes(self) -> int:
        return sum(gpu.free_bytes for gpu in self.gpus)

    @property
    def ram_usable_bytes(self) -> int | None:
        if self.ram_total_bytes is None or self.ram_available_bytes is None:
            return None
        reserve = max(RAM_RESERVE_MIN_BYTES, int(self.ram_total_bytes * RAM_RESERVE_FRACTION))
        return max(0, self.ram_available_bytes - reserve)


@dataclass(frozen=True, slots=True)
class Footprint:
    weights_bytes: int
    kv_cache_bytes: int
    num_ctx: int
    kv_basis: str  # "model_geometry" or "fallback_constant"

    @property
    def total_bytes(self) -> int:
        return self.weights_bytes + self.kv_cache_bytes


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    model: str
    outcome: str
    reasons: tuple[str, ...]
    required_bytes: int
    footprint: Footprint | None
    vram_free_bytes: int
    ram_usable_bytes: int | None
    unload_candidates: tuple[str, ...] = ()
    resident: tuple[str, ...] = field(default=())

    @property
    def admitted(self) -> bool:
        return self.outcome in ADMITTING

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["admitted"] = self.admitted
        return payload


class ResourceDenied(RuntimeError):
    """The host refused to load a model because it cannot fit."""

    def __init__(self, decision: AdmissionDecision) -> None:
        super().__init__("; ".join(decision.reasons) or "insufficient resources")
        self.decision = decision


def _geometry_value(model_info: dict[str, Any], suffix: str) -> int | None:
    for key, value in model_info.items():
        if key.endswith(suffix) and isinstance(value, int) and value > 0:
            return value
    return None


def estimate_footprint(size_bytes: int, model_info: dict[str, Any] | None,
                       num_ctx: int | None) -> Footprint:
    """Estimate load size: weight file plus an f16 key/value cache for ``num_ctx``."""
    context = int(num_ctx or DEFAULT_NUM_CTX)
    info = model_info or {}
    layers = _geometry_value(info, ".block_count")
    width = _geometry_value(info, ".embedding_length")
    heads = _geometry_value(info, ".attention.head_count")
    kv_heads = _geometry_value(info, ".attention.head_count_kv") or heads
    if layers and width and heads and kv_heads:
        kv_width = width * kv_heads // heads
        per_token, basis = layers * kv_width * 2 * 2, "model_geometry"  # K and V, 2 bytes each
    else:
        per_token, basis = FALLBACK_KV_BYTES_PER_TOKEN, "fallback_constant"
    return Footprint(int(max(0, size_bytes) * WEIGHT_OVERHEAD), per_token * context, context, basis)


def _format(value: int) -> str:
    return f"{value / GIB:.1f} GiB"


def decide(model: str, footprint: Footprint, host: HostResources,
           resident: list[ResidentModel]) -> AdmissionDecision:
    """Pure admission decision; all inputs are measurements or stated estimates."""
    names = tuple(item.name for item in resident)
    need = footprint.total_bytes

    def result(outcome: str, *reasons: str, unload: tuple[str, ...] = ()) -> AdmissionDecision:
        return AdmissionDecision(model, outcome, tuple(reasons), need, footprint,
                                 host.vram_free_bytes, host.ram_usable_bytes, unload, names)

    if model in names:
        return AdmissionDecision(model, ADMIT, ("model is already resident in Ollama",), 0,
                                 footprint, host.vram_free_bytes, host.ram_usable_bytes, (), names)
    if footprint.weights_bytes <= 0:
        return result(ADMIT_UNMEASURED, "model size is unknown; admission was not evaluated")
    ram = host.ram_usable_bytes
    if not host.gpus and ram is None:
        return result(ADMIT_UNMEASURED, "host memory could not be measured; admission was not evaluated")

    others = [item for item in resident if item.name != model]
    vram_now = max(0, host.vram_free_bytes - VRAM_HEADROOM_BYTES) if host.gpus else 0
    vram_reclaimable = sum(item.size_vram_bytes for item in others) if host.gpus else 0
    ram_reclaimable = sum(max(0, item.size_bytes - item.size_vram_bytes) for item in others)
    ram_now = ram or 0
    estimate = f"estimated load {_format(need)} at num_ctx={footprint.num_ctx} ({footprint.kv_basis})"

    if host.gpus and need <= vram_now:
        return result(ADMIT, f"{estimate} fits in free VRAM ({_format(vram_now)} usable)")
    by_cost = tuple(item.name for item in sorted(others, key=lambda item: -item.size_vram_bytes))
    if host.gpus and need <= vram_now + vram_reclaimable:
        return result(
            ADMIT_AFTER_UNLOAD,
            f"{estimate} fits in VRAM only after unloading resident models",
            unload=by_cost,
        )
    if not host.gpus and need <= ram_now:
        return result(ADMIT, f"{estimate} fits in usable RAM ({_format(ram_now)}); no GPU detected")
    if ram is None:
        return result(ADMIT_UNMEASURED,
                      f"{estimate} exceeds usable VRAM and system RAM could not be measured")
    if need <= vram_now + ram_now:
        return result(
            ADMIT_DEGRADED,
            f"{estimate} exceeds usable VRAM ({_format(vram_now)}); layers will run on the CPU "
            f"and inference will be slower",
        )
    if need <= vram_now + vram_reclaimable + ram_now + ram_reclaimable:
        return result(
            ADMIT_AFTER_UNLOAD,
            f"{estimate} fits only after unloading resident models and with CPU offload",
            unload=by_cost,
        )
    return result(
        DENY,
        f"{estimate} exceeds usable VRAM ({_format(vram_now + vram_reclaimable)} incl. reclaimable) "
        f"plus usable RAM ({_format(ram_now + ram_reclaimable)})",
        "choose a smaller model or quantization, or lower num_ctx",
    )


class AdmissionController:
    """Collects live measurements and applies :func:`decide`."""

    def __init__(self, client: Any, *,
                 snapshot: Callable[[], dict[str, Any]] | None = None) -> None:
        self.client = client
        self._snapshot = snapshot or SystemTelemetry().snapshot

    def resident(self) -> list[ResidentModel]:
        try:
            running = self.client.running_models()
        except Exception:  # noqa: BLE001 - Ollama being unreachable is handled by readiness checks
            return []
        models: list[ResidentModel] = []
        for item in running:
            name = str(item.get("name") or item.get("model") or "")
            if name:
                models.append(ResidentModel(name, int(item.get("size") or 0),
                                            int(item.get("size_vram") or 0)))
        return models

    def footprint(self, model: str, num_ctx: int | None) -> Footprint:
        size = 0
        for item in self.client.list_models():
            if str(item.get("name") or item.get("model") or "") == model:
                size = int(item.get("size") or 0)
                break
        try:
            info = self.client.show_model(model).get("model_info")
        except Exception:  # noqa: BLE001 - geometry is optional; the fallback constant is used
            info = None
        return estimate_footprint(size, info if isinstance(info, dict) else None, num_ctx)

    def evaluate(self, model: str, num_ctx: int | None = None) -> AdmissionDecision:
        host = HostResources.from_snapshot(self._snapshot())
        return decide(model, self.footprint(model, num_ctx), host, self.resident())

    def require(self, model: str, num_ctx: int | None = None) -> AdmissionDecision:
        decision = self.evaluate(model, num_ctx)
        if not decision.admitted:
            raise ResourceDenied(decision)
        return decision


class ResourceScheduler:
    """Admission queue for concurrent model loads in one process.

    ``AdmissionController`` judges one load against the machine as it is now.
    Concurrent agents need more: two children that each fit alone may not fit
    together, a model that is reserved but still loading is not yet visible in
    Ollama's resident list, and a model being used by a running agent must not
    be evicted to make room for another.

    ``lease(model)`` therefore:

    * subtracts every reserved-but-not-yet-resident model from free VRAM/RAM;
    * when the load fits only after unloading, unloads resident models that no
      lease holds (automatic unload), then re-evaluates;
    * when the models that must go are leased, waits for a release (queue),
      up to ``timeout``;
    * refuses a load that cannot fit even with everything unloaded.

    Leases on the same model share it. ``require`` is the gate-compatible
    entry point: inside a lease held by the calling thread it admits at once,
    otherwise it falls back to plain admission.
    """

    def __init__(self, controller: AdmissionController, *,
                 unload: Callable[[str], None] | None = None,
                 poll_seconds: float = 0.5,
                 notify: Callable[[str], None] | None = None) -> None:
        import threading

        self.controller = controller
        self.notify = notify or (lambda _message: None)
        self._unload = unload if unload is not None else getattr(controller.client, "unload", None)
        self.poll_seconds = poll_seconds
        self._condition = threading.Condition()
        self._leases: dict[str, int] = {}
        self._reserved: dict[str, Footprint] = {}
        self._local = threading.local()
        self.unloaded: list[str] = []  # audit trail of automatic unloads

    def _held(self) -> dict[str, int]:
        held = getattr(self._local, "held", None)
        if held is None:
            held = self._local.held = {}
        return held

    def _evaluate(self, model: str, num_ctx: int | None) -> AdmissionDecision:
        resident = self.controller.resident()
        resident_names = {item.name for item in resident}
        host = HostResources.from_snapshot(self.controller._snapshot())  # noqa: SLF001
        pending = sum(footprint.total_bytes for name, footprint in self._reserved.items()
                      if name not in resident_names and name != model)
        if pending:
            host = _minus(host, pending)
        footprint = self.controller.footprint(model, num_ctx)
        # A resident model that a lease holds cannot be counted as reclaimable.
        return decide(model, footprint, host, resident)

    def acquire(self, model: str, num_ctx: int | None = None, *, timeout: float = 600.0,
                cancel_event: Any | None = None) -> AdmissionDecision:
        import time

        deadline = time.monotonic() + max(0.0, timeout)
        attempted_unload: set[str] = set()
        with self._condition:
            while True:
                if model in self._leases:
                    decision = AdmissionDecision(model, ADMIT, ("shares a model another agent holds",),
                                                 0, self._reserved.get(model), 0, None)
                    break
                decision = self._evaluate(model, num_ctx)
                blocked_by_leases = decision.outcome == DENY and bool(self._leases)
                if decision.outcome == DENY and not blocked_by_leases:
                    raise ResourceDenied(decision)  # cannot fit even with nothing else loaded
                if decision.outcome not in (ADMIT_AFTER_UNLOAD, DENY):
                    break
                idle = [name for name in decision.unload_candidates
                        if name not in self._leases and name not in attempted_unload]
                if idle and self._unload is not None:
                    for name in idle:
                        attempted_unload.add(name)
                        try:
                            self._unload(name)
                            self.unloaded.append(name)
                            self.notify(f"unloaded idle model {name} to admit {model}")
                        except Exception as exc:  # noqa: BLE001 - re-evaluation decides next
                            self.notify(f"could not unload {name}: {exc}")
                    continue
                if not blocked_by_leases and not any(
                        name in self._leases for name in decision.unload_candidates):
                    break  # nothing leased stands in the way; Ollama evicts on load
                remaining = deadline - time.monotonic()
                if remaining <= 0 or (cancel_event is not None and cancel_event.is_set()):
                    raise ResourceDenied(AdmissionDecision(
                        model, DENY,
                        (*decision.reasons,
                         f"waited {timeout:.0f}s for leased models to be released: "
                         + ", ".join(sorted(self._leases))),
                        decision.required_bytes, decision.footprint, decision.vram_free_bytes,
                        decision.ram_usable_bytes, decision.unload_candidates, decision.resident))
                if not getattr(self._local, "announced_wait", False):
                    self._local.announced_wait = True
                    self.notify(f"{model} queued: waiting for "
                                + ", ".join(sorted(self._leases)) + " to be released")
                self._condition.wait(min(self.poll_seconds, remaining))
            self._local.announced_wait = False
            self._leases[model] = self._leases.get(model, 0) + 1
            if decision.footprint is not None:
                self._reserved.setdefault(model, decision.footprint)
            held = self._held()
            held[model] = held.get(model, 0) + 1
        return decision

    def release(self, model: str) -> None:
        with self._condition:
            count = self._leases.get(model, 0) - 1
            if count <= 0:
                self._leases.pop(model, None)
                self._reserved.pop(model, None)
            else:
                self._leases[model] = count
            held = self._held()
            if held.get(model, 0) <= 1:
                held.pop(model, None)
            else:
                held[model] -= 1
            self._condition.notify_all()

    def lease(self, model: str, num_ctx: int | None = None, *, timeout: float = 600.0,
              cancel_event: Any | None = None) -> Any:
        from contextlib import contextmanager

        @contextmanager
        def _lease() -> Any:
            decision = self.acquire(model, num_ctx, timeout=timeout, cancel_event=cancel_event)
            try:
                yield decision
            finally:
                self.release(model)

        return _lease()

    def active(self) -> dict[str, int]:
        with self._condition:
            return dict(self._leases)

    def require(self, model: str, num_ctx: int | None = None) -> AdmissionDecision:
        if self._held().get(model):
            return AdmissionDecision(model, ADMIT, ("reserved by the resource scheduler",),
                                     0, self._reserved.get(model), 0, None)
        return self.controller.require(model, num_ctx)


def _minus(host: HostResources, reserved: int) -> HostResources:
    """Host resources with ``reserved`` bytes already spoken for (VRAM first, then RAM)."""
    gpus = list(host.gpus)
    left = reserved
    adjusted: list[GpuState] = []
    for gpu in gpus:
        take = min(left, gpu.free_bytes)
        adjusted.append(GpuState(gpu.name, gpu.total_bytes, gpu.free_bytes - take))
        left -= take
    available = host.ram_available_bytes
    if left and available is not None:
        available = max(0, available - left)
    return HostResources(host.ram_total_bytes, available, tuple(adjusted))
