"""Small, typed Ollama HTTP client with explicit timeout and error handling."""

from __future__ import annotations

from threading import Event, Thread
from typing import Any

import httpx


class OllamaError(RuntimeError):
    """Raised when Ollama is unreachable or returns an invalid response."""


class OllamaClient:
    def __init__(self, base_url: str = "http://127.0.0.1:11434", timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = httpx.request(method, f"{self.base_url}{path}", json=payload,
                                     timeout=self.timeout,
                                     headers={"User-Agent": "11vated-Ollama-Workstation/0.1.0"})
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise OllamaError(f"Unexpected response from Ollama {path}: expected an object")
            return data
        except httpx.TimeoutException as exc:
            raise OllamaError(f"Ollama request timed out: {path}") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise OllamaError(f"Ollama returned HTTP {exc.response.status_code} for {path}: {detail}") from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Cannot connect to Ollama at {self.base_url}: {exc}") from exc
        except ValueError as exc:
            raise OllamaError(f"Ollama returned invalid JSON for {path}") from exc

    def health(self) -> bool:
        try:
            response = httpx.get(f"{self.base_url}/api/version", timeout=min(self.timeout, 5.0))
            response.raise_for_status()
            return bool(response.json().get("version"))
        except (httpx.HTTPError, ValueError, AttributeError):
            return False

    def version(self) -> str:
        return str(self._request("GET", "/api/version").get("version", "unknown"))

    def list_models(self) -> list[dict[str, Any]]:
        models = self._request("GET", "/api/tags").get("models", [])
        return models if isinstance(models, list) else []

    def show_model(self, name: str) -> dict[str, Any]:
        return self._request("POST", "/api/show", {"model": name, "verbose": True})

    def running_models(self) -> list[dict[str, Any]]:
        models = self._request("GET", "/api/ps").get("models", [])
        return models if isinstance(models, list) else []

    def chat(self, model: str, messages: list[dict[str, Any]], *,
             tools: list[dict[str, Any]] | None = None,
             output_format: dict[str, Any] | str | None = None,
             options: dict[str, Any] | None = None,
             keep_alive: str = "5m",
             cancel_event: Event | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model, "messages": messages,
                                   "stream": cancel_event is not None,
                                   "keep_alive": keep_alive}
        if tools is not None:
            payload["tools"] = tools
        if output_format is not None:
            payload["format"] = output_format
        if options:
            payload["options"] = options
        if cancel_event is not None:
            return self._chat_stream(payload, cancel_event)
        return self._request("POST", "/api/chat", payload)

    @staticmethod
    def _merge_stream_message(target: dict[str, Any], incoming: dict[str, Any]) -> None:
        """Merge one Ollama chat stream fragment into the accumulated assistant message."""
        for key in ("role",):
            if incoming.get(key):
                target[key] = incoming[key]
        for key in ("content", "thinking"):
            fragment = incoming.get(key)
            if isinstance(fragment, str) and fragment:
                target[key] = str(target.get(key) or "") + fragment
        if isinstance(incoming.get("images"), list):
            target.setdefault("images", []).extend(incoming["images"])
        incoming_calls = incoming.get("tool_calls")
        if isinstance(incoming_calls, list):
            calls = target.setdefault("tool_calls", [])
            for index, call in enumerate(incoming_calls):
                if not isinstance(call, dict):
                    continue
                while len(calls) <= index:
                    calls.append({"function": {"name": "", "arguments": {}}})
                destination = calls[index]
                function = call.get("function") or {}
                if not isinstance(function, dict):
                    continue
                target_function = destination.setdefault("function", {})
                if function.get("name"):
                    target_function["name"] = function["name"]
                arguments = function.get("arguments")
                if isinstance(arguments, dict):
                    target_function.setdefault("arguments", {}).update(arguments)
                elif isinstance(arguments, str):
                    target_function["arguments"] = (
                        str(target_function.get("arguments") or "") + arguments
                    )

    def _chat_stream(self, payload: dict[str, Any], cancel_event: Event) -> dict[str, Any]:
        """Read Ollama's NDJSON chat stream and close the HTTP stream on cancellation."""
        if cancel_event.is_set():
            raise OllamaError("Ollama chat request was cancelled before it started")
        headers = {"User-Agent": "11vated-Ollama-Workstation/0.2.0"}
        metadata: dict[str, Any] = {}
        message: dict[str, Any] = {"role": "assistant", "content": ""}
        stream_finished = Event()
        cancellation_detected = Event()
        watcher: Thread | None = None
        try:
            with httpx.stream(
                "POST", f"{self.base_url}/api/chat", json=payload,
                timeout=self.timeout, headers=headers,
            ) as response:
                response.raise_for_status()

                def close_stream_when_cancelled() -> None:
                    while not stream_finished.wait(0.05):
                        if cancel_event.is_set():
                            cancellation_detected.set()
                            response.close()
                            return

                watcher = Thread(
                    target=close_stream_when_cancelled,
                    name="ollama-stream-cancel",
                    daemon=True,
                )
                watcher.start()
                try:
                    for line in response.iter_lines():
                        if cancel_event.is_set():
                            cancellation_detected.set()
                            break
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                        except ValueError as exc:
                            raise OllamaError("Ollama returned invalid JSON in its chat stream") from exc
                        if not isinstance(chunk, dict):
                            raise OllamaError("Ollama returned a non-object chat stream event")
                        if chunk.get("error"):
                            raise OllamaError(f"Ollama stream error: {str(chunk['error'])[:500]}")
                        streamed_message = chunk.get("message")
                        if isinstance(streamed_message, dict):
                            self._merge_stream_message(message, streamed_message)
                        metadata.update({key: value for key, value in chunk.items() if key != "message"})
                        if chunk.get("done") is True:
                            break
                finally:
                    stream_finished.set()
                    if watcher is not None:
                        watcher.join(timeout=0.5)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise OllamaError(
                f"Ollama returned HTTP {exc.response.status_code} for /api/chat: {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            if cancel_event.is_set() or cancellation_detected.is_set():
                raise OllamaError("Ollama chat request was cancelled") from exc
            raise OllamaError(f"Streaming Ollama request failed: {exc}") from exc
        if cancel_event.is_set() or cancellation_detected.is_set():
            raise OllamaError("Ollama chat request was cancelled")
        if not metadata.get("done", False):
            raise OllamaError("Ollama chat stream ended before its final event")
        return {**metadata, "message": message}
