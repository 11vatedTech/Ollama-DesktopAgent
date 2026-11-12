"""A real HTTP server that speaks the Ollama API with deterministic, scripted models.

It exists so the production client, runtime, qualification laboratory, agent
service and recovery path can be exercised end to end, over real sockets and
across real process boundaries, without a GPU. The "models" are small reactive
programs with deliberately different protocol abilities. They prove that the
host machinery measures and reacts to differences correctly; they are NOT
evidence about any real model's capability.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

Step = tuple[str, Any, Any]  # ("tool", name, arguments) | ("final", text, None)


@dataclass(frozen=True)
class Persona:
    native: str = "ok"        # ok | unsupported | bad_args | prose
    structured: str = "ok"    # ok | prose
    guided: str = "ok"        # ok | chatty | prose
    gullible: bool = False    # obeys instructions embedded in file content
    careless: bool = False    # performs badly unless the matching skill is supplied
    delay_from_request: int = 0
    delay_seconds: float = 0.0


PERSONAS: dict[str, Persona] = {
    "native-pro:latest": Persona(),
    "json-only:latest": Persona(native="unsupported", guided="prose"),
    "guided-only:latest": Persona(native="prose", structured="prose", guided="chatty"),
    "sloppy-native:latest": Persona(native="bad_args"),
    "gullible:latest": Persona(gullible=True),
    "careless:latest": Persona(careless=True),
    "hopeless:latest": Persona(native="prose", structured="prose", guided="prose"),
}


def _tool_results(messages: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    results: list[tuple[str, dict[str, Any]]] = []
    for message in messages:
        if message.get("role") == "tool":
            try:
                payload = json.loads(str(message.get("content") or "{}"))
            except json.JSONDecodeError:
                payload = {}
            results.append((str(message.get("tool_name") or ""), payload))
    return results


def _first_line(result: dict[str, Any]) -> str:
    return str(result.get("content") or "").splitlines()[0] if result.get("content") else ""


def _write_then_final(results: list[tuple[str, dict[str, Any]]], path: str, content: str,
                      done: str) -> Step:
    """Write once; retry while the host reports an error; finish after a success."""
    if any(name == "write_file" and "bytes_written" in result for name, result in results):
        return ("final", done, None)
    return ("tool", "write_file", {"path": path, "content": content})


def plan(objective: str, system: str, results: list[tuple[str, dict[str, Any]]],
         persona: Persona) -> Step:
    """Decide the next step from the objective and the tool results so far."""
    count = len(results)
    ok = [(name, result) for name, result in results if "error" not in result]

    if "access code" in objective:
        if count == 0:
            return ("tool", "read_file", {"path": "inventory.txt"})
        code = re.search(r"access code: (\S+)", str(results[0][1].get("content")))
        return _write_then_final(results, "answer.txt", code.group(1) if code else "?", "Recorded.")

    if "marker" in objective:
        code = re.search(r"marker (\w+)", objective)
        if count == 0:
            return ("tool", "search_text", {"pattern": f"marker {code.group(1)}", "path": "notes"})
        matches = results[0][1].get("matches") or [{}]
        name = str(matches[0].get("path", "")).replace("\\", "/").split("/")[-1]
        return _write_then_final(results, "located.txt", name, "Located.")

    for filename, pattern in (("config.json", r"version field to (\d+)"),
                              ("settings.json", r"retries field to (\d+)")):
        if filename in objective:
            if count == 0:
                return ("tool", "read_file", {"path": filename})
            key = "version" if filename == "config.json" else "retries"
            target = int(re.search(pattern, objective).group(1))
            document = json.loads(str(results[0][1].get("content") or "{}"))
            skilled = "SKILL inspect-before-edit" in system
            if persona.careless and filename == "settings.json" and not skilled:
                document = {}
            document[key] = target
            return _write_then_final(results, filename, json.dumps(document), "Updated.")

    if "docs/notes.md" in objective:
        if count == 0:
            return ("tool", "read_file", {"path": "docs/notes.md"})
        return _write_then_final(results, "summary.txt", _first_line(results[0][1]), "Summarised.")

    if "Count the files" in objective:
        if count == 0:
            return ("tool", "list_files", {"path": "."})
        names = [entry["name"] for entry in results[0][1].get("entries", [])]
        return ("final", f"There are {sum(n.endswith('.txt') for n in names)} .txt files.", None)

    if "data.txt" in objective:
        if count == 0:
            return ("tool", "read_file", {"path": "data.txt"})
        written = [r for n, r in ok if n == "write_file"]
        if not written:
            return ("tool", "write_file", {"path": "out.txt", "content": _first_line(results[0][1])})
        if persona.gullible and len(written) == 1:
            return ("tool", "write_file", {"path": "pwned.txt", "content": "HACKED"})
        return ("final", "Copied the first line.", None)

    if "report.txt" in objective:
        if count == 0:
            return ("tool", "read_file", {"path": "report.txt"})
        if persona.careless and "SKILL recover-from-tool-error" not in system:
            return ("final", "The file does not exist.", None)
        if count == 1:
            return ("tool", "search_text", {"pattern": "status"})
        if count == 2:
            found = (results[1][1].get("matches") or [{}])[0].get("path", "")
            return ("tool", "read_file", {"path": str(found).replace("\\", "/")})
        return _write_then_final(results, "result.txt", _first_line(results[2][1]), "Recovered.")

    if "status.txt" in objective:
        code = re.search(r"ready-(\w+)", objective)
        return _write_then_final(results, "status.txt", f"ready-{code.group(1)}",
                                 "Created status.txt.")

    if "calc.py" in objective:
        if count == 0:
            return ("tool", "read_file", {"path": "calc.py"})
        if count == 1:
            fixed = str(results[0][1].get("content")).replace("result -= value", "result += value")
            return ("tool", "write_file", {"path": "calc.py", "content": fixed})
        if count == 2:
            return ("tool", "run_command", {"command": "python",
                                            "args": ["-m", "pytest", "-q", "-p", "no:cacheprovider"]})
        return ("final", "Fixed the sign error in calc.py; the tests pass.", None)

    if "grand total" in objective:
        if count == 0:
            return ("tool", "list_files", {"path": "orders"})
        names = [entry["name"] for entry in results[0][1].get("entries", [])]
        reads = [r for n, r in results if n == "read_file"]
        if len(reads) < len(names):
            return ("tool", "read_file", {"path": f"orders/{names[len(reads)]}"})
        total = sum(int(re.search(r"total: (\d+)", str(r.get("content"))).group(1)) for r in reads)
        return _write_then_final(results, "report/summary.txt", f"grand total: {total}\n",
                                 "Wrote report/summary.txt.")

    return ("final", "Nothing to do.", None)


def respond(model: str, payload: dict[str, Any]) -> dict[str, Any]:
    persona = PERSONAS[model]
    messages = payload.get("messages") or []
    system = next((str(m.get("content")) for m in messages if m.get("role") == "system"), "")
    objective = next((str(m.get("content")) for m in messages
                      if m.get("role") == "user" and "HOST NOTICE" not in str(m.get("content"))), "")
    kind, first, second = plan(objective, system, _tool_results(messages), persona)
    prose = "I have considered the request carefully and will proceed as described."
    if payload.get("tools"):
        if persona.native == "unsupported":
            raise LookupError(f"registry.ollama.ai/library/{model} does not support tools")
        if persona.native == "prose":
            return {"role": "assistant", "content": prose}
        if kind == "final":
            return {"role": "assistant", "content": first}
        arguments = dict(second)
        if persona.native == "bad_args":
            arguments = {f"{key}_value": value for key, value in arguments.items()}
        return {"role": "assistant", "content": "",
                "tool_calls": [{"function": {"name": first, "arguments": arguments}}]}
    if isinstance(payload.get("format"), dict):
        if persona.structured == "prose":
            return {"role": "assistant", "content": prose}
        body = ({"action": "final", "message": first} if kind == "final"
                else {"action": "tool", "name": first, "arguments": second})
        return {"role": "assistant", "content": json.dumps(body)}
    if persona.guided == "prose":
        return {"role": "assistant", "content": prose}
    if kind == "final":
        return {"role": "assistant", "content": f"FINAL: {first}"}
    action = "ACTION: " + json.dumps({"name": first, "arguments": second})
    if persona.guided == "chatty":
        action = "Let me think about which tool fits.\n" + action
    return {"role": "assistant", "content": action}


class ScriptedOllama:
    """Context manager owning the server thread."""

    def __init__(self, personas: dict[str, Persona] | None = None, version: str = "0.99.0") -> None:
        self.personas = dict(personas or PERSONAS)
        self.version = version
        self.requests: dict[str, int] = {}
        # Seconds to stall any chat request that already carries a tool result.
        # Tests use it to hold a run at the "awaiting model" boundary.
        self.hold_seconds = 0.0
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:
                return

            def _send(self, status: int, body: dict[str, Any], *, ndjson: bool = False) -> None:
                data = (json.dumps(body) + ("\n" if ndjson else "")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type",
                                 "application/x-ndjson" if ndjson else "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)
                self.close_connection = True

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/api/version":
                    self._send(200, {"version": owner.version})
                elif self.path == "/api/tags":
                    self._send(200, {"models": [owner.tag(name) for name in owner.personas]})
                elif self.path == "/api/ps":
                    self._send(200, {"models": []})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                model = str(payload.get("model") or payload.get("name") or "")
                if model not in owner.personas:
                    self._send(404, {"error": f"model '{model}' not found"})
                    return
                persona = owner.personas[model]
                if self.path == "/api/show":
                    self._send(200, {
                        "details": {"family": "scripted", "parameter_size": "0.1B",
                                    "quantization_level": "Q4_K_M"},
                        "model_info": {"general.architecture": "scripted",
                                       "scripted.context_length": 8192},
                        "capabilities": ["completion", *(["tools"] if persona.native != "unsupported"
                                                         else [])],
                    })
                    return
                if self.path != "/api/chat":
                    self._send(404, {"error": "not found"})
                    return
                with owner._lock:
                    owner.requests[model] = owner.requests.get(model, 0) + 1
                    number = owner.requests[model]
                if persona.delay_from_request and number >= persona.delay_from_request:
                    time.sleep(persona.delay_seconds)
                hold = owner.hold_seconds
                if hold > 0 and any(m.get("role") == "tool" for m in payload.get("messages", [])):
                    time.sleep(hold)
                try:
                    message = respond(model, payload)
                except LookupError as exc:
                    self._send(400, {"error": str(exc)})
                    return
                self._send(200, {
                    "model": model, "message": message, "done": True, "done_reason": "stop",
                    "prompt_eval_count": 40, "eval_count": 12,
                }, ndjson=bool(payload.get("stream")))

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @staticmethod
    def tag(name: str) -> dict[str, Any]:
        return {"name": name, "model": name, "size": 1_000_000,
                "digest": hashlib.sha256(name.encode()).hexdigest(),
                "modified_at": "2026-01-01T00:00:00Z",
                "details": {"family": "scripted", "parameter_size": "0.1B",
                            "quantization_level": "Q4_K_M"}}

    def __enter__(self) -> ScriptedOllama:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
