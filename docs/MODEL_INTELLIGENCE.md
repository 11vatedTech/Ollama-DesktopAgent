# Model Intelligence & Identity Engine

## Mission

Turn an Ollama model tag into an evidence-backed operational profile and reusable agent identity package. Distinguish publisher disclosures, local runtime metadata, observed behavior and unknowns.

## Three intelligence layers

### Origin dossier

The v0.2 model-card parser lives in `profiling/model_card.py`. It extracts structured publisher metadata and clearly labelled Markdown sections from a repository explicitly selected by the user. Search candidates are ranked using transparent lexical similarity hints, but the user must confirm the upstream repository. Re-attaching a refreshed card replaces previous card-derived evidence rather than duplicating it.

Collect, when available: exact Ollama tag/digest, parameter size, quantization, family, architecture metadata, context metadata, declared capabilities, publisher, canonical model-card URL, base model/lineage, disclosed datasets, fine-tuning approach, published evaluations, intended uses, limitations and license.

The local Ollama API cannot reveal undisclosed training data. A model name, architecture or tokenizer must never be used to invent a training corpus. Multiple plausible upstream matches are candidates; the user explicitly links the correct repository. Model-card extraction records description, intended use, limitations, training/evaluation sections, declared datasets, base model, languages, license, tags, retrieval timestamp and content hash. Publisher disclosures remain distinct from observed behavior.

### Empirical capability map

Initial v0.1 screen:

| Capability | Initial assessment |
|---|---|
| Instruction following | Exact-output smoke test |
| Structured output | JSON Schema output probe |
| Structured actions | Schema-constrained agent-action probe |
| Native tool calling | Harmless single-function invocation probe; tool not executed |
| Code generation | Parse generated Python; inspect function shape; do not execute generated code |
| Code comprehension | Deterministic code-tracing question |
| Reasoning | Single controlled syllogism smoke test |
| Mathematics | Deterministic arithmetic question |
| Vision | Declared metadata only; quality not assessed |
| Long context, research, creative writing, multilingual, planning, error recovery, audio | Not assessed in v0.1 |

A single passed smoke test proves only that a model passed that one item once. It is not a broad ability guarantee. The v0.2 profiler supports 1–5 repeated trials per smoke dimension and stores method, attempts, pass count, confidence, duration, numeric/timing metrics, evidence and limitations. Failed trials count as zero in aggregate scores; raw model messages and tool arguments are not stored in the metric record. Full evaluation requires task samples, repeated trials, held-out tasks, adversarial cases and independent scoring.

### Model intelligence and routing

`models/model_intelligence.py` inspects native Ollama `/api/show` metadata and records family, architecture, parameter count, quantization, context length, embedding width, attention/KV geometry, declared tools/vision/embedding capabilities and an inferred memory envelope. Declared metadata and inferred estimates are kept separate from observed capability evidence; missing geometry remains unknown rather than zero.

`profile-all` discovers the complete live inventory and writes one persistent profile per installed artifact; a metadata-inspection failure is recorded as an unknown fallback rather than silently dropping the model. `models/routing.py` ranks installed artifacts against required capabilities, soft prompt-derived preferred capabilities, context, memory ceiling, observed quality, speed preference, exclusions and preferred models. Benchmarks attached to a profile provide a quality/speed fallback when direct capability evidence is sparse. Eligible routes form an explicit fallback chain. Declared-only capability can support a provisional route, but `require_verified=True` excludes it and the decision reports why. Routing does not guarantee execution: the selected installed digest is still checked before an agent starts.

### Reproducible local benchmark runner

The CLI exposes a versioned `11vated-core@1.0.0` suite through `11vated benchmark MODEL --suite core --repeats 3`. Its six synthetic deterministic items cover exact instruction compliance, arithmetic, code-trace reasoning, JSON extraction, code comprehension and planning-order constraints. Host-side evaluators independently score exact strings, numeric answers, JSON shape/value and ordered terms; the model is never asked to grade itself.

The runner clamps repeats to 1–5 and writes a JSON report bound to the exact installed Ollama model digest and a SHA-256 hash of the test/prompt/evaluator manifest. Per-case results include attempts, pass counts, raw per-trial outcomes (with credential-like values filtered from previews), Wilson 95% intervals, median/p95 latency, generation throughput and a host snapshot. Errors and cancellations do not produce a certified partial report. The bounded report summary is persisted in the profile's benchmark history and latest-benchmark fields; the full report remains the immutable artifact.

This suite deliberately does not claim broad role qualification: six small synthetic cases are a local regression/diagnostic layer, not a replacement for audited specialist evaluations, held-out tasks, repository repair, real tool use, browser/computer use or production agent tasks. Never compare scores across suite versions or manifest hashes as though they were identical experiments.

Live local evidence (Ollama 0.40.16, `qwen2.5-coder:7b`, two repeats): 16 probe calls at 44.22 tok/s mean; reasoning, structured output and structured actions passed; instruction following, code comprehension and mathematics were partial; code generation failed; native tool calling and vision were unsupported; long-context, research, planning, error recovery and audio remain unassessed. The core benchmark scored 8/12 (66.67) with manifest `9e68732a7f0a648ab6cfaa6584d897be1abed8bbb32de59142a8eead52947f79`. The other 14 models have metadata profiles only. These are local measurements, not certification.

### Operational dossier

Record cold/warm load time, prompt and generation throughput, token counts, VRAM/RAM, context behavior, concurrent memory pressure and scheduling constraints. Include hardware, driver, Ollama version, model digest and benchmark configuration. The v0.2 increment adds dependency-free host snapshots (OS, CPU count, system memory) and best-effort NVIDIA telemetry through nvidia-smi, plus Ollama generation/prompt token counters, load duration and derived generation throughput. GPU telemetry is unavailable when nvidia-smi is absent; unknown values remain null rather than fabricated.

## Professional identity

A title describes an operational specialty, not intrinsic model personality. It is generated from observed capability dimensions, with evidence rationale and confidence. The expanded role taxonomy includes Repository Cartographer, Precision Implementation Engineer, Native Tool Operations Specialist, Structured Workflow Operator, Evidence Synthesis Analyst, Precision Data Extraction Specialist, Creative Narrative Architect, Multilingual Communication Specialist, Strategic Workflow Planner, Quantitative Reasoning Specialist and Visual Intelligence Inspector.

A role recommendation includes supporting capabilities, evidence count/coverage, confidence, provisional/certified state, missing assessments, exact model tag and digest. Unknown dimensions are neither zero nor 100.

## Agent Identity Package

The current schema-1.0 export includes schema/package metadata, timestamp, identity title and roles, rationale/confidence, Ollama provider/model/digest binding, model family/size/quantization, agentability mode/status, default permission label, capability records (including attempts, passes, method, confidence, numeric metrics, evidence and limitations), publisher/origin evidence, known limitations and profile version. The exporter does not assign a workspace or grant tools. Resource envelope, explicit memory-policy manifest, signed certification, harness qualification and MCP grants remain future versioned package fields.

## Memory and workforce

The v0.2 memory service is local SQLite with explicit user-authored writes, scoped user/project/agent records, six note categories, tags, confidence, timestamps, access counts and archive/forget semantics. Project memory can be added and archived from the desktop. Agent runs retrieve project-path notes, user-default notes and exact-model-digest notes. Retrieval filters by terms in SQLite before deterministic token/tag-overlap ranking and currently caps the candidate set at 500 matching records per recall. There is no embedding/vector search, automatic conversation-to-memory write, consolidation or contradiction resolution. Run checkpointing is a runtime concern: it snapshots the execution contract, not memory. Retrieved text is treated as untrusted context, not system authority.

The workforce matcher derives role requirements from the same canonical role taxonomy used by identity assignment. It ranks profiles by capability score, sample confidence and requirement coverage, reports missing dimensions and keeps results provisional until an explicit role-qualification process exists. `WorkforceSelector` exposes selection/rejection reasons and prefers distinct models when possible.

The host-owned orchestrator executes validated plans with sequential children, exact installed-digest binding, per-task step/time budgets, cancellation, approval boundaries, artifact checks and independent verifier verdicts. It records task and child events in a hash-chained durable journal. This is not an OS sandbox: children share the assigned workspace, the read-only registry is a tool policy, and parallel/distributed scheduling, per-child resource quotas, latency history and hardware admission control remain open.

## Research provenance

Public model-card discovery is opt-in. Hugging Face search results are candidate matches. The user chooses the correct repository ID before attaching. Record repository ID, canonical URL, metadata retrieval time and content hash. Training-data claims must cite explicit card sections or publisher documents. Unavailable information remains unknown.

## Certification ladder

1. **Discovered** — model exists in Ollama inventory.
2. **Documented** — dossier includes primary sources.
3. **Profiled** — low-cost probes completed.
4. **Role-qualified** — multi-sample role evaluation passes thresholds.
5. **Agent-qualified** — exact model + harness + toolset completes repeated end-to-end tasks.
6. **Production-certified** — security, resource, reliability and regression gates pass on a declared hardware/software matrix.

The 0.1.0 baseline established discovery and agent-runtime foundations. The v0.2 development increment adds structured publisher research, repeated smoke profiling, local telemetry, scoped memory, fail-closed model-digest binding, model-intelligence routing, encrypted checkpoint recovery, atomic run leases and host-controlled workforce orchestration. It still does not claim role qualification, agent qualification or production certification.
