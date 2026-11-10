"""Least-privilege workspace tools for agent execution."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any


class ToolError(RuntimeError):
    """Raised for invalid, unauthorized or failed tool operations."""


class WorkspaceTools:
    MAX_READ_BYTES = 64_000
    MAX_WRITE_BYTES = 1_000_000
    MAX_RESULTS = 200

    def __init__(self, workspace: Path, *, allow_shell: bool = False,
                 allowed_commands: tuple[str, ...] = (),
                 approval: Callable[[str], bool] | None = None) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.allow_shell = allow_shell
        self.allowed_commands = {Path(x).name.lower() for x in allowed_commands}
        self.approval = approval or (lambda _description: False)

    def _path(self, relative: str) -> Path:
        candidate = (self.workspace / relative).resolve()
        try:
            candidate.relative_to(self.workspace)
        except ValueError as exc:
            raise ToolError("Path is outside the assigned workspace") from exc
        return candidate

    def list_files(self, path: str = ".") -> dict[str, Any]:
        directory = self._path(path)
        if not directory.exists() or not directory.is_dir():
            raise ToolError("Directory does not exist")
        items = []
        for child in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            try:
                child.resolve().relative_to(self.workspace)
            except ValueError:
                continue
            items.append({"name": child.name, "type": "directory" if child.is_dir() else "file",
                          "size": child.stat().st_size if child.is_file() else None})
            if len(items) >= self.MAX_RESULTS:
                break
        return {"path": path, "entries": items}

    def read_file(self, path: str) -> dict[str, Any]:
        target = self._path(path)
        if not target.is_file():
            raise ToolError("File does not exist")
        if target.stat().st_size > self.MAX_READ_BYTES:
            raise ToolError(f"File exceeds {self.MAX_READ_BYTES} byte read limit")
        return {"path": path, "content": target.read_text(encoding="utf-8", errors="replace")}

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        target = self._path(path)
        encoded = content.encode("utf-8")
        if len(encoded) > self.MAX_WRITE_BYTES:
            raise ToolError(f"File exceeds {self.MAX_WRITE_BYTES} byte write limit")
        if target.exists() and not self.approval(f"Overwrite workspace file: {path}"):
            raise ToolError("Overwrite was not approved")
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".11vated-write-", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return {"path": path, "bytes_written": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest()}

    def search_text(self, pattern: str, path: str = ".") -> dict[str, Any]:
        root = self._path(path)
        if not root.exists():
            raise ToolError("Search path does not exist")
        matches: list[dict[str, Any]] = []
        files = [root] if root.is_file() else root.rglob("*")
        for file in files:
            if not file.is_file() or file.stat().st_size > self.MAX_READ_BYTES:
                continue
            if any(part in {".git", ".venv", "node_modules", "__pycache__"} for part in file.parts):
                continue
            try:
                file.resolve().relative_to(self.workspace)
                if file.is_symlink():
                    continue
                lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for number, line in enumerate(lines, 1):
                if pattern.casefold() in line.casefold():
                    matches.append({"path": str(file.relative_to(self.workspace)), "line": number,
                                    "text": line[:500]})
                    if len(matches) >= self.MAX_RESULTS:
                        return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": False}

    def run_command(self, command: str, args: list[str] | None = None,
                    cwd: str = ".") -> dict[str, Any]:
        executable = Path(command).name.lower()
        if not self.allow_shell or executable not in self.allowed_commands:
            raise ToolError("Command execution is disabled or the executable is not allow-listed")
        working_directory = self._path(cwd)
        if not working_directory.is_dir():
            raise ToolError("Command working directory does not exist")
        argv = [command, *(str(value) for value in (args or []))]
        if not self.approval("Run approved command: " + json.dumps(argv)):
            raise ToolError("Command execution was not approved")
        try:
            result = subprocess.run(
                argv, cwd=working_directory, capture_output=True, text=True,
                timeout=90, check=False, shell=False,
                env={"PATH": os.environ.get("PATH", ""), "PYTHONUTF8": "1",
                     "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"},
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolError("Command exceeded the 90-second execution limit") from exc
        return {"command": argv, "return_code": result.returncode,
                "stdout": result.stdout[-12000:], "stderr": result.stderr[-12000:]}

    def definitions(self) -> list[dict[str, Any]]:
        definitions = [
            self._function("list_files", "List files and directories in the assigned workspace.",
                           {"path": {"type": "string", "default": "."}}, []),
            self._function("read_file", "Read a UTF-8 text file from the assigned workspace.",
                           {"path": {"type": "string"}}, ["path"]),
            self._function("write_file", "Write a UTF-8 file inside the workspace. Existing-file overwrites require approval.",
                           {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
            self._function("search_text", "Search workspace text files for a literal phrase.",
                           {"pattern": {"type": "string"}, "path": {"type": "string", "default": "."}}, ["pattern"]),
        ]
        if self.allow_shell:
            definitions.append(self._function(
                "run_command", "Run a separately approved allow-listed executable without a shell.",
                {"command": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}},
                 "cwd": {"type": "string", "default": "."}}, ["command"],
            ))
        return definitions

    @staticmethod
    def _function(name: str, description: str, properties: dict[str, Any],
                  required: list[str]) -> dict[str, Any]:
        return {"type": "function", "function": {"name": name, "description": description,
                "parameters": {"type": "object", "properties": properties,
                               "required": required, "additionalProperties": False}}}

    def _validate_arguments(self, name: str, arguments: dict[str, Any]) -> None:
        """Validate model-generated arguments against the host-owned tool schema."""
        definition = next(
            (item for item in self.definitions()
             if item["function"]["name"] == name), None
        )
        if definition is None:
            raise ToolError(f"Tool is not registered or enabled: {name}")
        schema = definition["function"]["parameters"]
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        missing = required - set(arguments)
        if missing:
            raise ToolError(f"Missing required argument(s) for {name}: {', '.join(sorted(missing))}")
        if schema.get("additionalProperties") is False:
            extra = set(arguments) - set(properties)
            if extra:
                raise ToolError(f"Unexpected argument(s) for {name}: {', '.join(sorted(extra))}")
        for key, value in arguments.items():
            property_schema = properties.get(key, {})
            expected = property_schema.get("type")
            valid = (
                expected is None
                or expected == "string" and isinstance(value, str)
                or expected == "object" and isinstance(value, dict)
                or expected == "array" and isinstance(value, list)
                or expected == "integer" and isinstance(value, int) and not isinstance(value, bool)
                or expected == "number" and isinstance(value, (int, float)) and not isinstance(value, bool)
                or expected == "boolean" and isinstance(value, bool)
            )
            if not valid:
                raise ToolError(f"Argument {key!r} for {name} must be {expected}")
            if expected == "array":
                item_type = property_schema.get("items", {}).get("type")
                if item_type == "string" and not all(isinstance(item, str) for item in value):
                    raise ToolError(f"Every item in {key!r} for {name} must be a string")
            allowed = property_schema.get("enum")
            if allowed is not None and value not in allowed:
                raise ToolError(f"Argument {key!r} for {name} is outside its allowed values")

    def invoke(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ToolError("Tool arguments must be a JSON object")
        self._validate_arguments(name, arguments)
        actions = {
            "list_files": lambda: self.list_files(str(arguments.get("path", "."))),
            "read_file": lambda: self.read_file(str(arguments["path"])),
            "write_file": lambda: self.write_file(str(arguments["path"]), str(arguments["content"])),
            "search_text": lambda: self.search_text(str(arguments["pattern"]), str(arguments.get("path", "."))),
            "run_command": lambda: self.run_command(str(arguments["command"]),
                                                      list(arguments.get("args") or []),
                                                      str(arguments.get("cwd", "."))),
        }
        action = actions.get(name)
        if action is None:
            raise ToolError(f"Tool is not registered: {name}")
        try:
            return action()
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolError(f"Invalid arguments for {name}: {exc}") from exc
