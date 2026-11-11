# Changelog

## Unreleased — Intelligence Interface 2.0 verification

- Added `profile-all` and robust inventory-wide metadata profiles: all 15 live Ollama artifacts have persistent profiles; metadata-only profiles explicitly mark capabilities unassessed and survive a single metadata-inspection failure.
- Added model benchmark evidence to profiles and routing. `benchmark` and the Model Intelligence page persist a bounded latest/history summary tied to the suite manifest and model digest; routing can use benchmark quality/speed as a fallback and prompt-derived preferred capabilities as soft ranking signals.
- Reworked workforce verification around host-owned immutable manifests and a typed `11vated-verifier/1` contract. The verifier receives read-only tools, the host validates every cited path/digest, writes the audit itself, and fail-closes on tamper, missing/empty artifacts, malformed evidence, timeouts or substantive FAIL.
- Added one provider-enforced schema formatting pass when a verifier returns prose, plus a narrow host-authority rule for factual “missing/empty” claims contradicted by the manifest. The raw model verdict, contradiction, manifest and host decision remain visible in `workforce/verification.json`.
- Added adversarial verifier tests for fabricated references, missing evidence, malformed JSON, mutation after snapshot, contradictory FAIL, read-only enforcement and bounded failures.
- Improved responsive behavior: Command hero and action/stat rows reflow, Model Intelligence becomes a vertical inventory/dossier split below 880 px, and status pills wrap. Captured and inspected 24 native Windows screenshots at desktop, compact, 1280 × 800, 1024 × 768 and 900 × 700.
- Added chat user-message editing with branch-before-message behavior, branch/export controls and clearer artifact inspection.
- Expanded intelligence-core state mapping and canonical aliases; removed legacy aliases that could disagree with the formal state set.
- Live local evidence: Ollama 0.40.16, 15 models; `qwen2.5-coder:7b` empirical profile (16 probes, 44.22 tok/s mean), core benchmark 8/12 (66.67) with manifest `9e68732a7f0a648ab6cfaa6584d897be1abed8bbb32de59142a8eead52947f79`; CLI run `23dc040f-1aa3-40d1-882a-b3a2c7f6b6e0` and desktop run `beee5cd1-342a-449b-994b-e47d3a53d9be` completed with `verification_passed=true`; failure run `fb65bbb1-064e-4be6-9937-a40c2d3acd30` correctly blocked a claimed write that never occurred.
- Full local suite: 185 passed, 4 skipped; 10 focused UI tests; Ruff and `compileall` clean. Hosted CI run 43056402479 passed all four Windows/Ubuntu Python 3.11/3.12 jobs, including Windows desktop import and UI tests.

## 0.2.0.dev0 — 2026-11-10

Development increment on the 0.1.0 foundation. This is a pre-release engineering branch and is not production-certified.

### Desktop intelligence interface
- Rebuilt the PySide6 desktop shell as the nine-workspace 11vatedTech Intelligence Interface: Command Center, Advanced Chat, Model Intelligence, Workforce Studio, Agent Operations, Integration Hub, System Laboratory, Run History & Recovery, and Settings.
- Added shared design tokens, cards, gauges, pills, headers, list delegates and a custom `QPainter` intelligence core. Motion targets a 16 ms cadence, with a 750 ms reduced-motion mode and responsive page transitions.
- Wired the interface to real local state: live Ollama inventory and version, saved profiles, host telemetry, durable runs, recovery candidates, MCP configuration and workforce task events. No reasoning progress, task success or host activity is fabricated.
- Added streamed model chat, cooperative stop/cancel, persisted SQLite conversations, model/agent/routed/workforce modes, context and attachment inspection, model filters and comparison controls.
- Added responsive Command, Chat and Workforce layouts, two-line selected list rows, effective numeric Settings fields and independent Workforce column scrolling.
- Added seven focused headless UI tests plus a reproducible native Windows screenshot gallery covering all nine workspaces and three compact views.
- Live Windows checks: a real `qwen2.5-coder:7b` chat returned `STREAM_OK`; an active streamed response was stopped and surfaced as `Generation stopped`; a bounded three-task workforce delivery ran and correctly failed closed at `verification_failed` when the verifier returned `VERDICT: FAIL`. The UI paint-only sample averaged 5.77 ms per 410 × 410 core frame (p95 8.01 ms); end-to-end FPS is not claimed.

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
- Hardened shutdown: failed initialization closes its connection, concurrent reconnects are serialized, and a transparent stdio supervisor owns a kill-on-close Job Object on Windows and a dedicated process group on POSIX so even a graceful SDK shutdown cannot orphan grandchildren. Process-tree cleanup is verified against real stdio children/grandchildren on Linux and Windows. CI now installs the `mcp` extra so the suite runs on the full matrix. Frozen PyInstaller builds fall back to the SDK cleanup until a supervisor executable is bundled.

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
- Local Windows Python 3.12.13 passed 164 tests with 4 skipped; Python 3.11.15 passed 157 with 5 skipped. Ruff and `compileall` pass, including the real MCP transport suite. The seven focused desktop UI tests pass offscreen. Opt-in live Ollama tests pass with `OLLAMA_LIVE=1`. Hosted CI run 42977464138 passed all four Windows/Ubuntu Python 3.11/3.12 jobs, including the seven desktop UI tests on Windows; local results remain distinct from hosted coverage.
- Added structured JSONL logging with rotation and redaction, plus a `doctor` diagnostic snapshot. Added measured startup, inventory, routing, tool, checkpoint, recovery, concurrency and desktop-construction timings in Operations documentation.
- Added real MCP stdio and Streamable HTTP transport certification tests; mocks remain supplemental.

### Not yet production-certified
OS-enforced sandboxing, per-child workspace/resource isolation, parallel/distributed scheduling, full specialist evaluation adapters, role/agent qualification, full MCP server/ACP support, browser/computer-use adapters, whole-database encryption, vector memory retrieval, SBOM and signed distribution remain open release gates. The implemented workforce and routing paths are locally verified, not production-certified.
