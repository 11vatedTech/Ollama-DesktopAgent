# Changelog

## 0.2.0.dev0 — 2026-11-10

Development increment on the 0.1.0 foundation. This is a pre-release engineering branch and is not production-certified.

### Model intelligence
- Structured Hugging Face publisher dossier with explicit upstream repository confirmation, publisher training/evaluation disclosures, declared metadata, structured model-index metrics and SHA-256 provenance.
- Evidence-based expanded professional identity taxonomy and canonical role mapping.
- 1–5 repeated local smoke trials with attempts, pass counts, confidence, failure-aware aggregation and local host/NVIDIA/Ollama inference telemetry.
- Identity package exports capability attempt/pass counts and safe numeric metrics.
- CLI and desktop agent execution refuse to use a saved profile if its installed Ollama model name/digest cannot be verified or has changed.

### Agent runtime
- Explicit host tool catalogue and response contracts for native function calls, structured JSON actions and guided ACTION/FINAL output.
- Host-owned tool-schema validation rejects missing/extra parameters, type mismatches and disabled tools. Credential/private-key paths are denied by default, with common environment-template exceptions.
- SQLite scoped project/user/agent memory with explicit notes, lexical recall, tags, archive semantics and untrusted-context handling. Credential-like values are redacted before saving memory.
- Desktop cancellation closes active streamed Ollama chat requests and is checked between tool operations. Approved commands receive the cancellation event and attempt process-tree cleanup (POSIX process groups; Windows taskkill best-effort). This is not an OS sandbox.
- Agent run events are committed incrementally to a durable SQLite journal; interrupted runs retain a crash-visible running state and ordered event history. The desktop includes a Run History view.
- Credential-like values are redacted from workspace/search/command output, persisted run objectives and event payloads, and final CLI/desktop responses. This is heuristic filtering, not complete DLP.
- Model-card research callbacks are bound to the initiating model, avoiding cross-model result races.

### Model intelligence, routing and orchestration
- Added `/api/show` model-intelligence metadata: family, architecture, parameter count, quantization, context, embedding/KV geometry, declared capabilities, inferred memory envelope and evidence levels.
- Added hardware/capability/context-aware model routing with observed-vs-declared distinction, explicit eligibility reasons, preferred-model ordering, fallback chain and inferred memory constraints.
- Added a host-validated workforce orchestrator: JSON plans, DAG validation, unique IDs, cycle rejection, task decomposition, sequential delegation, shared workspace, per-task step/time budgets, cancellation, retry-safe failure handling, approval boundaries, dependency context, independent verifier verdicts, host artifact checks and durable hash-chained task/child events.
- Added `WorkforceSelector` and CLI `workforce run/status`, `route` and `doctor` commands; added a desktop Workforce page with reviewed execution, run history and task/event inspection.
- Live Ollama evidence: a three-agent delivery run completed with a real model, root artifact, delivery report and independent `VERDICT: PASS`; step-limit and verifier failures are explicitly rejected.

### Durable execution recovery
- Checkpoints are now written by normal execution at three safe boundaries: before each provider request, before a tool batch and after a completed tool batch. Previously the persistence helper existed but was never invoked.
- Checkpoint payloads are encrypted at rest with AES-256-GCM and a separate 32-byte local key. Legacy plaintext rows remain readable for migration.
- Added atomic per-run leases for CLI/desktop resume and workforce execution. Live foreign leases are not stolen; expired leases are reclaimable.
- The checkpoint envelope SHA-256 binds the model digest, workspace, task, harness mode, tool-manifest hash, state, counters, creation/update timestamps and bounded payload, so altering a binding column fails closed.
- Recovery uses a dedicated protocol version, a 72-hour freshness window, run-status checks and exact task/harness/tool-manifest verification on every resume.
- An interrupted tool batch is durably marked `needs_review` and cannot be resumed or replayed automatically. Completed runs discard checkpoints; cancelled and failed runs retain them for review.
- Added `11vated recover --list`, `11vated recover RUN_ID` and `11vated recover RUN_ID --discard`, plus a desktop Run History recovery panel with explicit confirmation.
- Resume reuses the original run identifier and step budget, appends a `resumed` journal event and never grants a fresh budget.

### MCP transport certification
- Added a versioned transport contract (`agents/mcp_contract.py`) and real SDK v2 certification tests using a launched stdio server and a uvicorn-hosted Streamable HTTP server. No fake MCP client is used for transport evidence.
- Covered initialization, discovery, invocation, per-call timeouts, cooperative cancellation, abrupt process death, transport interruption, reconnection, concurrent clients, duplicate requests, partial tool failure, large-result bounding, credential redaction, schema-drift refusal, repeated lifecycle and process/session cleanup.
- Reconnection now replaces only the transport session for future calls and refuses changed approved-tool manifests. Timed-out, cancelled or crashed calls are reported as unknown outcome and are never automatically replayed.
- Added bounded lifecycle diagnostics with transport contract metadata, connection state, exposed/approved counts, reconnect count and retained redacted failure type/message.
- Hardened shutdown: failed initialization closes its connection, concurrent reconnects are serialized, and a transparent Windows stdio supervisor owns a second kill-on-close Job Object so an SDK shutdown race cannot orphan grandchildren. Process-tree cleanup is verified against real stdio children/grandchildren. CI now installs the `mcp` extra so the suite runs on the full matrix. Frozen PyInstaller builds fall back to the SDK Job Object until a supervisor executable is bundled.

### Benchmarking
- Added the versioned six-case 11vated-core local benchmark runner with independent host-side exact, numeric, JSON-schema and ordered-term scoring, repeated trials, Wilson confidence intervals, latency/throughput metrics and model/manifest digest binding.
- Added CLI benchmark reports with redacted output previews; this is a local diagnostic suite, not external benchmark certification.

### Desktop, CLI and operations
- Desktop model-card research, repeated profiling, model dossier view, scoped project memory management, cooperative stop control and runtime digest verification.
- CLI repeated profiling and memory-aware agent runtime.
- Development package version set to 0.2.0.dev0.

### Research and quality
- Added dated primary-source research and benchmark-validity register.
- Documented explicit release gates, competitor constraints, reproducibility manifest and measured autonomy metric.
- Local Windows Python 3.11 and 3.12 runs pass compile, Ruff and pytest, including the real MCP transport suite. Opt-in live Ollama tests pass with `OLLAMA_LIVE=1`. Hosted CI results are reported separately after push; Linux coverage is not inferred from local runs.
- Added structured JSONL logging with rotation and redaction, plus a `doctor` diagnostic snapshot. Added measured startup, inventory, routing, tool, checkpoint, recovery, concurrency and desktop-construction timings in Operations documentation.
- Added real MCP stdio and Streamable HTTP transport certification tests; mocks remain supplemental.

### Not yet production-certified
OS-enforced sandboxing, per-child workspace/resource isolation, parallel/distributed scheduling, full specialist evaluation adapters, role/agent qualification, full MCP server/ACP support, browser/computer-use adapters, whole-database encryption, vector memory retrieval, SBOM and signed distribution remain open release gates. The implemented workforce and routing paths are locally verified, not production-certified.
