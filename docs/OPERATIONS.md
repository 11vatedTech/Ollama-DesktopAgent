# Operations, diagnostics and local performance

**Status:** Operational evidence from a single Windows development host. These
numbers are not a cloud benchmark, a service-level agreement or a production
certification.

## Structured logs

The CLI and desktop configure a local JSON Lines log at:

```text
<application data>/logs/workstation.jsonl
```

On Windows the default application data root is under the per-user
`11vated-Ollama-Workstation/11vatedTech` directory. The logger uses a rotating
5 MiB file with three backups. Each record contains a timestamp, level, logger,
message, optional event name, run ID, task ID, status and bounded metadata.

Log messages and metadata pass through the same credential-pattern redaction
used for model-visible output. Secret values are heuristically removed, and
metadata keys matching common credential names are replaced with
`[REDACTED]`. This is defence in depth, not a guarantee that arbitrary secret
formats are safe to log.

## Diagnostic snapshot

```powershell
11vated doctor
```

`doctor` reports:

- application and Ollama version;
- Ollama connectivity and installed model names;
- SQLite integrity, profile/run/workforce counts;
- checkpoint-key presence (not its contents);
- log presence;
- local host/CPU/memory/NVIDIA telemetry when available.

It returns exit code 2 when Ollama is unhealthy or SQLite integrity fails.

## Measured local performance

Environment:

- Windows 11, Python 3.12.13
- Ollama 0.40.15, 15 installed models
- NVIDIA GeForce RTX 5070 Ti Laptop GPU, 12 GiB VRAM
- Local SSD; no network model calls in the microbenchmarks

Method: repeated operations with `time.perf_counter`; median reported. This is
a smoke measurement intended to expose gross regressions, not a statistically
controlled benchmark.

| Operation | Median | Range / notes |
|---|---:|---|
| CLI module import, fresh process | 949.53 ms | 3 samples: 935.54–965.66 ms |
| Ollama inventory (`/api/tags` adapter) | 199.92 ms | 3 samples: 184.39–221.54 ms, live provider |
| Route 15 models | 0.14 ms | 20 samples; 0.14–6.49 ms |
| Workspace write + read + search | 10.51 ms | 30 cycles; 9.12–16.65 ms |
| Encrypted checkpoint save + load | 15.60 ms | 30 cycles; 14.37–31.42 ms |
| Recovery candidate scan | 3.22 ms | 20 samples; 2.90–4.26 ms |
| Workforce plan validation/build | 0.03 ms | 50 samples; 0.03–0.15 ms |
| 64 concurrent checkpoint writes, 8 threads | 500.31 ms total | max worker 498.89 ms |
| Intelligence core paint, 410 × 410 offscreen | 5.43 ms median | 240 frames; mean 5.77 ms, p95 8.01 ms, max 9.84 ms; paint-only, not end-to-end FPS |

The live three-agent delivery workflow took 17 seconds end-to-end in the same
environment, including model inference, artifact writes and verification. See
[Workforce orchestration](WORKFORCE.md) for the evidence and run ID.

### Interpretation

- Process startup and Ollama inventory dominate first-use latency.
- Local routing and plan validation are effectively negligible.
- SQLite checkpoint encryption adds measurable work but remains well below the
  cost of a model call; concurrent writes serialize on SQLite and should not be
  treated as a high-throughput database benchmark.
- The intelligence core paint figure is a warm, offscreen `QPainter` sample at
  410 × 410. It covers only that widget's paint work and should not be read as a
  full-window frame rate, application startup time or GPU compositor result.

## Reliability checks

- Full local test suite: Python 3.12.13 (164 passed, 4 skipped) and 3.11.15
  (157 passed, 5 skipped).
- Seven focused desktop UI tests pass offscreen; 12 native Windows captures were
  visually inspected for the desktop and compact workspaces.
- Static checks: Ruff and `compileall`.
- Live provider tests: opt-in with `OLLAMA_LIVE=1`; metadata, chat and embeddings.
- Live desktop checks: `qwen2.5-coder:7b` returned `STREAM_OK`, an active stream
  stopped cleanly, and a three-task workforce run correctly failed closed when
  its verifier returned `VERDICT: FAIL`.
- Real MCP stdio and Streamable HTTP integration tests remain part of the suite
  when the `mcp` extra is installed.
- Hosted CI run 42977464138 passed all four matrix jobs (Ubuntu/Windows,
  Python 3.11/3.12). The Windows jobs ran seven desktop UI tests; Ubuntu 3.12
  reported 158 passed / 4 skipped in the full suite.
