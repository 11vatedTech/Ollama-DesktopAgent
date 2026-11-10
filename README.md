# 11vated Intelligence — Ollama Agent Workstation

**Any model. Any agent. Any workflow.**

A local-first, Windows-first desktop agent workstation from 11vatedTech. Its defining subsystem is the **Universal Agentification Engine**: inventory a model, research its origin, test its real capabilities, assign an evidence-backed professional identity, then equip it with a bounded execution harness.

> **Release status: v0.2 development increment on the 0.1.0 foundation — not production-certified.** The branch adds a structured publisher dossier, repeatable local profiling, hardware/inference telemetry, model-digest enforcement, scoped project memory, expanded identity/workforce roles, host-side tool-schema validation and a desktop/CLI workflow. The broader production gates are tracked in [Production Readiness](docs/PRODUCTION_READINESS.md).

## Product thesis

Ollama supplies local inference. This workstation provides the operational system around a model:

1. **Discover** installed models and inspect `/api/show` metadata.
2. **Research** upstream model cards; candidate matches are suggestions and require explicit user confirmation.
3. **Profile** low-cost tasks for instruction following, structured output, structured actions, native tools, code generation/comprehension, reasoning and arithmetic.
4. **Identify** a model-specific professional title, primary role and secondary strengths from observed results. Unknowns remain unknown.
5. **Agentify** using native tool calling where it works, otherwise structured actions or a guided text-action protocol.
6. **Execute** bounded tasks in a selected workspace with host-enforced tool validation and permissions.
7. **Verify and retain** run traces and model profiles in local SQLite.

## Architecture

`Desktop / CLI → Application services → Ollama provider + Model Intelligence Engine → Universal Agentification Engine → Agent runtime → Policy-enforced tools → Verification and evidence → SQLite`

The core is provider/harness/UI separated. The desktop shell does not duplicate inference or tool policy. Read [Architecture](docs/ARCHITECTURE.md), [Model Intelligence](docs/MODEL_INTELLIGENCE.md), [Production Readiness](docs/PRODUCTION_READINESS.md), the dated [Research Register](docs/RESEARCH_REGISTER.md) and the [Changelog](CHANGELOG.md).

## Implemented in the current development branch (pre-release v0.2)

| Capability | Status |
|---|---|
| Ollama health, model inventory, model metadata and loaded-model inspection | Implemented |
| Local model smoke probes | Implemented; not a full benchmark suite |
| Evidence-backed professional title and expanded role taxonomy | Implemented; provisional pending role qualification |
| Opt-in Hugging Face model-card research | Implemented in CLI and desktop; explicit repository selection required; lexical match is only a hint |
| Structured publisher dossier | Implemented for model description, intended use, limitations, training/evaluation disclosures, datasets, base model, languages, license and provenance hash |
| Repeatable smoke profiling | Implemented with 1–5 trials, pass-rate aggregation and confidence tracking; not a full benchmark suite |
| Host and inference telemetry | Implemented with system RAM/CPU snapshots, best-effort NVIDIA telemetry, Ollama token counters, load timing and derived generation throughput |
| Agent Identity Package export | Implemented |
| Native tool-call loop | Implemented |
| Structured/guided tool catalogue and action contracts | Implemented; host validation required before execution |
| Streaming cancellation and command cleanup | Desktop can close in-flight Ollama streams; command cancellation uses process-group/taskkill cleanup, best-effort only |
| Structured JSON action fallback and guided text action fallback | Implemented |
| Workspace-scoped file tools | Implemented with path containment, size limits, host-side JSON argument validation and default credential/private-key path denial |
| Optional allow-listed command tool | Implemented but disabled by default; configured executable resolution and human approval required; not a sandbox |
| SQLite profile, conversation, run and scoped-memory persistence | Implemented; project notes support explicit capture, lexical retrieval, listing and archive; no embeddings or automatic memory writes |
| PySide6 desktop shell and headless CLI | Implemented; Windows desktop import passes in CI; full GUI workflow QA on supported hardware remains |
| Evidence-based workforce ranking primitive | Implemented for software, repository, workflow, research, visual, quantitative, data, creative, multilingual and planning roles; qualification is explicit |
| Full MCP client/server ecosystem, browser/computer-use adapters, durable resumable runs, dynamic multi-agent scheduler, specialist benchmark adapters and role/agent certification | Roadmap |

## Requirements

- Python 3.11+ (3.12 recommended)
- Ollama installed and running
- One or more models pulled with Ollama
- Windows 11 is the primary desktop target; core services are cross-platform

## Install on Windows

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[desktop,dev]"
```

Check Ollama:

```powershell
ollama --version
ollama list
Invoke-RestMethod http://127.0.0.1:11434/api/version
```

Launch:

```powershell
python -m ollama_workstation
```

## CLI

```powershell
11vated models
11vated profile qwen2.5-coder:7b --metadata-only
11vated profile qwen2.5-coder:7b --repeats 3
11vated run qwen2.5-coder:7b .\agent-workspace "Inspect this project and summarize its architecture"
11vated research qwen2.5-coder:7b
11vated attach-model-card qwen2.5-coder:7b Qwen/Qwen2.5-Coder-7B-Instruct
11vated export-agent qwen2.5-coder:7b
```

The probe suite makes several model calls and loads the selected model. `--repeats 1..5` repeats each smoke probe and records pass rate, confidence and inference metrics. It remains an initial smoke-test battery, not a substitute for independently audited specialist evaluations. Read the [Research Register](docs/RESEARCH_REGISTER.md) before interpreting public benchmark scores.

## Privacy and security

- Inference targets the configured Ollama endpoint.
- Remote model research is opt-in and disabled by default.
- Hardware telemetry is local-only (system memory/CPU and best-effort NVIDIA nvidia-smi); no telemetry is uploaded to a service.
- Models are untrusted input generators; the host validates tool calls.
- File tools are workspace scoped and size limited.
- Overwrites require approval.
- Shell execution is disabled by default; when enabled it is allow-listed, approval-gated, uses no `shell=True`, and is time limited.
- No unrestricted browser, desktop-control, network or arbitrary Python-evaluation tool is exposed in this development increment. Shell stays disabled in the desktop agent.

Read [SECURITY.md](SECURITY.md) before enabling command execution or using sensitive workspaces. The desktop stop button closes the active streamed Ollama request and is checked between tool operations. Command termination is best-effort; this does not replace OS-level run isolation.

## Development

```powershell
python -m pip install -e ".[desktop,dev]"
pytest
ruff check .
python -m compileall -q src
```

CI is configured for Python 3.11/3.12 on Windows and Ubuntu. The last fully validated checkpoint passed compile, Ruff, tests and Windows desktop-import jobs (55 tests at that checkpoint). Newer cancellation, process cleanup and secret-path regression tests are awaiting a completed matrix before they are treated as validated. The Windows packaging workflow builds a PyInstaller distribution; installer signing and release certification remain open gates.

## Research sources

The project evaluates and benchmarks against open-source architecture candidates, including [Goose](https://github.com/aaif-goose/goose), [Interpreter Workstation](https://github.com/openinterpreter/interpreter-workstation), [OpenHands SDK](https://github.com/OpenHands/software-agent-sdk), [Codex CLI](https://github.com/openai/codex), [Grok Build](https://github.com/xai-org/grok-build), [Cline](https://github.com/cline/cline), [mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent), [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai), [LangGraph](https://github.com/langchain-ai/langgraph), [Letta](https://github.com/letta-ai/letta), [Mem0](https://github.com/mem0ai/mem0) and [Browser Use](https://github.com/browser-use/browser-use). These are research candidates, not bundled dependencies. See [the dated primary-source research register](docs/RESEARCH_REGISTER.md) for findings, risks and benchmarks.

## Licensing

This project is licensed under Apache-2.0. Third-party projects in the research register are not copied into this repository. Review each exact revision, license, notices, dependencies and provenance before reuse.
