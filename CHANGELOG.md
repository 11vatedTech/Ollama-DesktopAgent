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
- SQLite scoped project/user/agent memory with explicit notes, lexical recall, tags, archive semantics and untrusted-context handling.
- Desktop cancellation closes active streamed Ollama chat requests and is checked between tool operations. Approved commands receive the cancellation event and attempt process-tree cleanup (POSIX process groups; Windows taskkill best-effort). This is not an OS sandbox.
- Model-card research callbacks are bound to the initiating model, avoiding cross-model result races.

### Desktop, CLI and operations
- Desktop model-card research, repeated profiling, model dossier view, scoped project memory management, cooperative stop control and runtime digest verification.
- CLI repeated profiling and memory-aware agent runtime.
- Development package version set to 0.2.0.dev0.

### Research and quality
- Added dated primary-source research and benchmark-validity register.
- Documented explicit release gates, competitor constraints, reproducibility manifest and measured autonomy metric.
- CI has passed compile, Ruff, tests and Windows desktop import across Windows and Ubuntu with Python 3.11 and 3.12 at validated checkpoints. See PR checks for exact latest results.

### Not yet production-certified
OS-enforced sandboxing, durable run checkpoint/resume, full specialist evaluation adapters, role/agent qualification, multi-agent scheduling, MCP/ACP, browser/computer-use adapters, production telemetry/observability, SBOM and signed distribution remain open release gates.
