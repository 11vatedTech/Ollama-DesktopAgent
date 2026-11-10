# Development, validation and Windows release

## Repository layout

```
src/ollama_workstation/
  agents/       bounded runtime, workspace tools, identity export, workforce matching
  models/       domain schemas and evidence-backed identity rules
  ollama/       Ollama HTTP adapter
  profiling/    inventory, local probes and optional model-card research
  storage/      SQLite persistence
  ui/           PySide6 desktop and background workers
tests/          core service and security regression tests
docs/           architecture, research, benchmarking and release policy
.github/        CI and Windows package workflows
```

## Local environment

Python 3.12 is recommended on Windows 11.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[desktop,dev]"
```

## Validation

```powershell
python -m compileall -q src
ruff check .
pytest --cov=ollama_workstation --cov-report=term-missing
```

Core tests do not require Ollama or a GPU. Live integration tests should be opt-in and record endpoint version, model digest, hardware and task configuration.

## Manual Windows integration test

1. Install Ollama and pull a chat model.
2. Verify `http://127.0.0.1:11434/api/version`.
3. Launch `python -m ollama_workstation`.
4. Refresh inventory; compare model tag/digest with `ollama list`.
5. Profile a model; inspect its evidence and low-confidence smoke results.
6. Export and inspect its Agent Identity Package.
7. Use a disposable workspace for a read-only inspection task.
8. Test file creation and confirm overwrites are denied by default.
9. Confirm shell is not exposed when disabled.
10. Interrupt a run; verify no false completion is recorded.
11. Inspect SQLite profile and run records.
12. Repeat with a model that does not emit native tool calls to test fallback behavior.

## Desktop packaging

The Windows CI workflow builds a PyInstaller distribution. The build artifact is not a signed installer. A production release still requires clean Windows VM tests, code signing, updater, installer/uninstaller, upgrade/rollback and antivirus false-positive tests.

## Dependency policy

- Keep runtime dependencies small.
- Use optional extras for Qt, evaluation suites, browser automation and MCP.
- Pin direct dependencies for release builds and generate an SBOM.
- Review exact licenses/notices before adopting research code.
- Keep cloud providers optional and outside core agentification.
