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

A single passed smoke test proves only that a model passed that one item once. It is not a broad ability guarantee. Store method, attempts, pass count, confidence, duration, evidence and limitations. Full evaluation requires task samples, repeated trials, held-out tasks, adversarial cases and independent scoring.

### Operational dossier

Record cold/warm load time, prompt and generation throughput, token counts, VRAM/RAM, context behavior, concurrent memory pressure and scheduling constraints. Include hardware, driver, Ollama version, model digest and benchmark configuration. The v0.2 increment adds dependency-free host snapshots (OS, CPU count, system memory) and best-effort NVIDIA telemetry through nvidia-smi, plus Ollama generation/prompt token counters, load duration and derived generation throughput. GPU telemetry is unavailable when nvidia-smi is absent; unknown values remain null rather than fabricated.

## Professional identity

A title describes an operational specialty, not intrinsic model personality. It is generated from observed capability dimensions, with evidence rationale and confidence. The expanded role taxonomy includes Repository Cartographer, Precision Implementation Engineer, Native Tool Operations Specialist, Structured Workflow Operator, Evidence Synthesis Analyst, Precision Data Extraction Specialist, Creative Narrative Architect, Multilingual Communication Specialist, Strategic Workflow Planner, Quantitative Reasoning Specialist and Visual Intelligence Inspector.

A role recommendation includes supporting capabilities, evidence count/coverage, confidence, provisional/certified state, missing assessments, exact model tag and digest. Unknown dimensions are neither zero nor 100.

## Agent Identity Package

JSON includes schema version, timestamp, title, specialization, rationale, confidence, provider/model/digest binding, harness mode, tool permission default, capability records, origin evidence, limitations and profile version. Later versions may include approved skill manifests, MCP grants, memory policies and signed certification.

## Research provenance

Public model-card discovery is opt-in. Hugging Face search results are candidate matches. The user chooses the correct repository ID before attaching. Record repository ID, canonical URL, metadata retrieval time and content hash. Training-data claims must cite explicit card sections or publisher documents. Unavailable information remains unknown.

## Certification ladder

1. **Discovered** — model exists in Ollama inventory.
2. **Documented** — dossier includes primary sources.
3. **Profiled** — low-cost probes completed.
4. **Role-qualified** — multi-sample role evaluation passes thresholds.
5. **Agent-qualified** — exact model + harness + toolset completes repeated end-to-end tasks.
6. **Production-certified** — security, resource, reliability and regression gates pass on a declared hardware/software matrix.

0.1.0 implements discovery/profile foundations only. It does not claim role qualification, agent qualification or production certification.
