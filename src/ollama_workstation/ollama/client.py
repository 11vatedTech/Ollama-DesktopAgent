"""Small, typed Ollama HTTP client with explicit timeout and error handling."""

from __future__ import annotations

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
             keep_alive: str = "5m") -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model, "messages": messages,
                                   "stream": False, "keep_alive": keep_alive}
        if tools is not None:
            payload["tools"] = tools
        if output_format is not None:
            payload["format"] = output_format
        if options:
            payload["options"] = options
        return self._request("POST", "/api/chat", payload)
