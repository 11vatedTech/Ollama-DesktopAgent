# Production roadmap and acceptance gates

**North star:** a local-first workstation that turns compatible Ollama chat models into bounded, measurable agents and assembles qualified agents into reliable teams.

## Phase 0 — Foundation vertical slice (0.1.0)

Delivered: Python package, CLI, PySide6 shell, Ollama client, inventory/show/ps/chat, low-cost profile, evidence-aware identity, native-tool and structured-action agent loop, workspace tools, restricted command runner, SQLite persistence, workforce matcher, tests and CI.

Release acceptance still requires a clean Windows 11 installation with desktop dependencies and a real Ollama instance. This environment cannot certify hardware/runtime behavior.

## Phase 1 — Research-grade Model Intelligence

- Explicit upstream model-card linking and provenance store.
- Publisher/model-card ingestion with claim-level citations and section locators.
- Dataset disclosure parser with unknown as a first-class state.
- Model lineage graph and variant/digest tracking.
- Expanded test batteries across reasoning, code, extraction, math, multilingual, context, vision, audio and research grounding.
- Multiple trials, held-out tasks, confidence intervals and per-capability reliability.
- Windows/NVIDIA GPU/RAM telemetry and tokens/sec.
- Signed/hashed profile and identity packages.

Acceptance: every origin claim links to a source or raw test; no unsupported training claim; repeated tests reproduce within declared tolerances.

## Phase 2 — Universal Agentification Engine

- Native tool adapter.
- JSON Schema/constrained action adapter.
- Text/grammar action parser with repair/reject paths.
- Shell/ReAct harness in an OS-level sandbox.
- Optional code-action harness in a sandbox.
- Automatic harness selection based on measured compatibility and task outcomes.
- Tool conformance tests, retries and structured failure taxonomy.
- Per-model/quantization harness profiles.

Acceptance: fixed tasks run across models/harnesses, all actions host-validated, improvements against raw chat measured with all failures reported.

## Phase 3 — Production agent runtime

- Streaming UI and typed event bus.
- Durable resumable execution and cancellation.
- Context budgeting, compaction and retrieval.
- Skill lifecycle, versioning and regression.
- Approval queue on UI thread.
- Process isolation, resource limits and safe workspace snapshots.
- Run replay and immutable audit logs.

Acceptance: interrupted runs recover; destructive permissions cannot be bypassed; completion requires evidence.

## Phase 4 — Multi-agent workforce

- Planner, dispatcher and task graph.
- Role matcher with complementary skill selection.
- Specialist and reviewer agents.
- Shared project memory with scoped access.
- Message/event protocol and task contracts.
- Conflict resolution, artifact ownership and integration gates.
- VRAM-aware model scheduling.

Acceptance: teams outperform single-agent and static-routing baselines after accounting for latency, resources and human intervention.

## Phase 5 — Workstation environments

- MCP server, ACP and advanced MCP capability grants; stdio/Streamable HTTP client transports are implemented.
- ACP interoperability.
- Browser and Windows desktop adapters behind a permission broker.
- Git worktrees/checkpoints and diff review.
- Python/C++, Unreal, Blender, Roblox, ComfyUI and creative tool integrations.
- Source-grounded research and retrieval.

Acceptance: version pins, setup diagnostics, permissions, regression tasks and recovery for each environment.

## Phase 6 — Distribution and commercial release

- Signed Windows installer and update channel.
- Reproducible build, SBOM and dependency/license reports.
- Transparent privacy/telemetry controls.
- Crash reporting with local-first option.
- Resource/performance dashboards.
- Public benchmark methodology and reproducible leaderboard.
- Signed skill/plugin packages.
- Windows release matrix and accessibility pass.

Acceptance: security review, reliability gates, installer/uninstaller tests, upgrade/rollback and public documentation.

## Release gate definitions

- **Discovered:** inventory only.
- **Profiled:** low-cost probes completed.
- **Role-qualified:** repeated role suite and independent scoring pass.
- **Agent-qualified:** end-to-end suite passes for exact model digest/harness/toolset.
- **Production-certified:** security, resource, reliability, recovery and release matrix pass.

0.1.0 is a development foundation, not a production-certified product.
