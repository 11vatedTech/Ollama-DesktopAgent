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

### Durable execution recovery
- Checkpoints are now written by normal execution at three safe boundaries: before each provider request, before a tool batch and after a completed tool batch. Previously the persistence helper existed but was never invoked.
- The checkpoint envelope SHA-256 binds the model digest, workspace, task, harness mode, tool-manifest hash, state, counters, creation/update timestamps and bounded payload, so altering a binding column fails closed.
- Recovery uses a dedicated protocol version, a 72-hour freshness window, run-status checks and exact task/harness/tool-manifest verification on every resume.
- An interrupted tool batch is durably marked `needs_review` and cannot be resumed or replayed automatically. Completed runs discard checkpoints; cancelled and failed runs retain them for review.
- Added `11vated recover --list`, `11vated recover RUN_ID` and `11vated recover RUN_ID --discard`, plus a desktop Run History recovery panel with explicit confirmation.
- Resume reuses the original run identifier and step budget, appends a `resumed` journal event and never grants a fresh budget.

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
- CI has passed compile, Ruff, tests and Windows desktop import across Windows and Ubuntu with Python 3.11 and 3.12 at validated checkpoints. See PR checks for exact latest results.

### Not yet production-certified
OS-enforced sandboxing, multi-process resume leases, full specialist evaluation adapters, role/agent qualification, delegated multi-agent scheduling, MCP/ACP, browser/computer-use adapters, production telemetry/observability, SBOM and signed distribution remain open release gates.
