from __future__ import annotations

from dataclasses import dataclass
import json
from urllib import error as urllib_error
from urllib import request as urllib_request
from typing import Any, Protocol


class LLMProviderError(Exception):
    pass


class LLMConfigurationError(Exception):
    pass


class LLMProvider(Protocol):
    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, dict[str, Any]]:
        ...

    def complete_with_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        ...


@dataclass
class MistralProvider:
    api_key: str
    base_url: str
    timeout_seconds: int

    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, dict[str, Any]]:
        if not self.api_key:
            raise LLMConfigurationError("MISTRAL_API_KEY is missing")

        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        raw_payload = json.dumps(payload).encode("utf-8")
        http_request = urllib_request.Request(endpoint, data=raw_payload, headers=headers, method="POST")

        try:
            with urllib_request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
            data = json.loads(raw)
        except urllib_error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else ""
            raise LLMProviderError(f"Mistral request failed with status {exc.code}: {body}") from exc
        except urllib_error.URLError as exc:
            raise LLMProviderError(f"Mistral request failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise LLMProviderError("Mistral response is not valid JSON") from exc

        try:
            choice = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError("Unexpected Mistral response format") from exc

        text: str
        if isinstance(choice, str):
            text = choice
        elif isinstance(choice, list):
            text = "\n".join(
                part.get("text", "") for part in choice if isinstance(part, dict) and part.get("text")
            ).strip()
        else:
            text = str(choice)

        usage = data.get("usage", {}) if isinstance(data, dict) else {}
        return text.strip(), usage

    def complete_with_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise LLMConfigurationError("MISTRAL_API_KEY is missing")

        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "tools": tools,
            "tool_choice": "auto",
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        raw_payload = json.dumps(payload).encode("utf-8")
        http_request = urllib_request.Request(endpoint, data=raw_payload, headers=headers, method="POST")

        try:
            with urllib_request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
            data = json.loads(raw)
        except urllib_error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else ""
            raise LLMProviderError(f"Mistral tool request failed with status {exc.code}: {body}") from exc
        except urllib_error.URLError as exc:
            raise LLMProviderError(f"Mistral tool request failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise LLMProviderError("Mistral tool response is not valid JSON") from exc

        try:
            choice = data["choices"][0]
            message = choice.get("message", {})
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError("Unexpected Mistral tool response format") from exc

        usage = data.get("usage", {}) if isinstance(data, dict) else {}
        return {
            "message": message,
            "usage": usage,
            "finish_reason": choice.get("finish_reason"),
        }


@dataclass
class EchoProvider:
    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, dict[str, Any]]:
        user_messages = [m["content"] for m in messages if m.get("role") == "user"]
        return "\n\n".join(user_messages), {}

    def complete_with_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        user_messages = [str(m.get("content", "")) for m in messages if m.get("role") == "user"]
        return {
            "message": {
                "role": "assistant",
                "content": "\n\n".join(user_messages),
            },
            "usage": {},
            "finish_reason": "stop",
        }


def build_provider(*, provider_name: str, api_key: str | None, base_url: str, timeout_seconds: int) -> LLMProvider:
    normalized = (provider_name or "mistral").strip().lower()

    if normalized == "mistral":
        if not api_key:
            raise LLMConfigurationError("Provider is mistral but MISTRAL_API_KEY is not configured")
        return MistralProvider(
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )

    if normalized == "echo":
        return EchoProvider()

    raise LLMConfigurationError(f"Unsupported LLM provider '{provider_name}'")
