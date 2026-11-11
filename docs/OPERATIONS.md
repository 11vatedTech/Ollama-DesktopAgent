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
| Desktop window construction, offscreen | 49.34 ms | UI construction only; excludes model inventory network work and user interaction |

The live three-agent delivery workflow took 17 seconds end-to-end in the same
environment, including model inference, artifact writes and verification. See
[Workforce orchestration](WORKFORCE.md) for the evidence and run ID.

### Interpretation

- Process startup and Ollama inventory dominate first-use latency.
- Local routing and plan validation are effectively negligible.
- SQLite checkpoint encryption adds measurable work but remains well below the
  cost of a model call; concurrent writes serialize on SQLite and should not be
  treated as a high-throughput database benchmark.
- The desktop figure is not a cold application launch measurement because the
  Python process and Qt imports were already loaded.

## Reliability checks

- Full local test suite: Python 3.12 and 3.11.
- Static checks: Ruff and `compileall`.
- Live provider tests: opt-in with `OLLAMA_LIVE=1`; metadata, chat and embeddings.
- Real MCP stdio and Streamable HTTP integration tests remain part of the suite
  when the `mcp` extra is installed.
- Hosted CI results are reported separately after the branch is pushed; local
  results do not establish Linux or hosted-runner coverage.
