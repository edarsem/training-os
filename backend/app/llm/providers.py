from __future__ import annotations

from dataclasses import dataclass
import json
from urllib import error as urllib_error
from urllib import parse as urllib_parse
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


@dataclass
class GoogleProvider:
    api_key: str
    base_url: str
    timeout_seconds: int

    def _build_endpoint(self, model: str) -> str:
        normalized_base = self.base_url.rstrip("/")
        model_escaped = urllib_parse.quote(model, safe="")
        return f"{normalized_base}/models/{model_escaped}:generateContent?key={urllib_parse.quote(self.api_key, safe='')}"

    def _request(self, *, payload: dict[str, Any], model: str) -> dict[str, Any]:
        endpoint = self._build_endpoint(model)
        headers = {
            "Content-Type": "application/json",
        }
        raw_payload = json.dumps(payload).encode("utf-8")
        http_request = urllib_request.Request(endpoint, data=raw_payload, headers=headers, method="POST")

        try:
            with urllib_request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise LLMProviderError("Google response has unexpected format")
            return data
        except urllib_error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else ""
            raise LLMProviderError(f"Google request failed with status {exc.code}: {body}") from exc
        except urllib_error.URLError as exc:
            raise LLMProviderError(f"Google request failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise LLMProviderError("Google response is not valid JSON") from exc

    def _extract_usage(self, data: dict[str, Any]) -> dict[str, Any]:
        usage = data.get("usageMetadata")
        if not isinstance(usage, dict):
            return {}

        out: dict[str, Any] = {}
        if isinstance(usage.get("promptTokenCount"), int):
            out["prompt_tokens"] = usage["promptTokenCount"]
        if isinstance(usage.get("candidatesTokenCount"), int):
            out["completion_tokens"] = usage["candidatesTokenCount"]
        if isinstance(usage.get("totalTokenCount"), int):
            out["total_tokens"] = usage["totalTokenCount"]
        return out

    def _to_gemini_content_messages(self, messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
        system_text_parts: list[str] = []
        contents: list[dict[str, Any]] = []

        for message in messages:
            role = str(message.get("role") or "").strip().lower()
            content = message.get("content")

            if role == "system":
                if isinstance(content, str) and content.strip():
                    system_text_parts.append(content.strip())
                continue

            if role == "tool":
                name = str(message.get("name") or "tool")
                parsed_response: Any = content
                if isinstance(content, str):
                    try:
                        parsed_response = json.loads(content)
                    except json.JSONDecodeError:
                        parsed_response = {"text": content}
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": name,
                                    "response": (
                                        parsed_response if isinstance(parsed_response, dict) else {"data": parsed_response}
                                    ),
                                }
                            }
                        ],
                    }
                )
                continue

            if role == "assistant":
                parts: list[dict[str, Any]] = []
                if isinstance(content, str) and content.strip():
                    parts.append({"text": content})

                tool_calls = message.get("tool_calls")
                if isinstance(tool_calls, list):
                    for call in tool_calls:
                        if not isinstance(call, dict):
                            continue

                        gemini_part = call.get("gemini_part") if isinstance(call.get("gemini_part"), dict) else None
                        if gemini_part and isinstance(gemini_part.get("functionCall"), dict):
                            parts.append(gemini_part)
                            continue

                        gemini_function_call = (
                            call.get("gemini_function_call")
                            if isinstance(call.get("gemini_function_call"), dict)
                            else None
                        )
                        if gemini_function_call and gemini_function_call.get("name"):
                            parts.append({"functionCall": gemini_function_call})
                            continue

                        function = call.get("function") if isinstance(call.get("function"), dict) else {}
                        function_name = function.get("name")
                        arguments_raw = function.get("arguments", {})
                        if not function_name:
                            continue

                        arguments: dict[str, Any]
                        if isinstance(arguments_raw, str):
                            try:
                                parsed = json.loads(arguments_raw)
                                arguments = parsed if isinstance(parsed, dict) else {}
                            except json.JSONDecodeError:
                                arguments = {}
                        elif isinstance(arguments_raw, dict):
                            arguments = arguments_raw
                        else:
                            arguments = {}

                        function_call_payload: dict[str, Any] = {
                            "name": str(function_name),
                            "args": arguments,
                        }

                        thought_signature = call.get("thought_signature")
                        function_call_part: dict[str, Any] = {
                            "functionCall": function_call_payload,
                        }

                        if isinstance(thought_signature, str) and thought_signature.strip():
                            function_call_payload["thoughtSignature"] = thought_signature
                            function_call_part["thoughtSignature"] = thought_signature

                        parts.append(function_call_part)

                if parts:
                    contents.append({"role": "model", "parts": parts})
                continue

            if isinstance(content, str) and content.strip():
                contents.append({"role": "user", "parts": [{"text": content}]})

        system_text = "\n\n".join(system_text_parts).strip() if system_text_parts else None
        return system_text, contents

    def _sanitize_schema_for_gemini(self, schema: Any) -> Any:
        if isinstance(schema, dict):
            sanitized: dict[str, Any] = {}
            for key, value in schema.items():
                if key in {"additionalProperties", "$schema"}:
                    continue
                sanitized[key] = self._sanitize_schema_for_gemini(value)
            return sanitized
        if isinstance(schema, list):
            return [self._sanitize_schema_for_gemini(item) for item in schema]
        return schema

    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, dict[str, Any]]:
        if not self.api_key:
            raise LLMConfigurationError("GOOGLE_API_KEY is missing")

        system_text, contents = self._to_gemini_content_messages(messages)
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}

        data = self._request(payload=payload, model=model)

        candidates = data.get("candidates")
        if not isinstance(candidates, list) or len(candidates) == 0:
            raise LLMProviderError("Unexpected Google response format: missing candidates")

        first = candidates[0] if isinstance(candidates[0], dict) else {}
        content = first.get("content") if isinstance(first.get("content"), dict) else {}
        parts = content.get("parts") if isinstance(content.get("parts"), list) else []

        text = "\n".join(
            str(part.get("text", ""))
            for part in parts
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ).strip()

        return text, self._extract_usage(data)

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
            raise LLMConfigurationError("GOOGLE_API_KEY is missing")

        system_text, contents = self._to_gemini_content_messages(messages)

        function_declarations: list[dict[str, Any]] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            if str(tool.get("type", "")).lower() != "function":
                continue
            function = tool.get("function") if isinstance(tool.get("function"), dict) else None
            if not function:
                continue
            function_name = function.get("name")
            if not function_name:
                continue
            function_declarations.append(
                {
                    "name": str(function_name),
                    "description": str(function.get("description") or ""),
                    "parameters": self._sanitize_schema_for_gemini(function.get("parameters"))
                    if isinstance(function.get("parameters"), dict)
                    else {},
                }
            )

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }

        if function_declarations:
            payload["tools"] = [{"functionDeclarations": function_declarations}]
            payload["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}

        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}

        data = self._request(payload=payload, model=model)

        candidates = data.get("candidates")
        if not isinstance(candidates, list) or len(candidates) == 0:
            raise LLMProviderError("Unexpected Google tool response format: missing candidates")

        first = candidates[0] if isinstance(candidates[0], dict) else {}
        content = first.get("content") if isinstance(first.get("content"), dict) else {}
        parts = content.get("parts") if isinstance(content.get("parts"), list) else []

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for index, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text)

            function_call = part.get("functionCall")
            if isinstance(function_call, dict) and function_call.get("name"):
                arguments = function_call.get("args") if isinstance(function_call.get("args"), dict) else {}
                thought_signature = function_call.get("thoughtSignature")
                if not isinstance(thought_signature, str):
                    thought_signature = function_call.get("thought_signature")
                if not isinstance(thought_signature, str):
                    thought_signature = part.get("thoughtSignature")
                if not isinstance(thought_signature, str):
                    thought_signature = part.get("thought_signature")
                tool_calls.append(
                    {
                        "id": f"google-call-{index}",
                        "type": "function",
                        "function": {
                            "name": str(function_call.get("name")),
                            "arguments": json.dumps(arguments, ensure_ascii=False),
                        },
                        "thought_signature": thought_signature,
                        "gemini_function_call": function_call,
                        "gemini_part": part,
                    }
                )

        usage = self._extract_usage(data)
        message: dict[str, Any] = {
            "role": "assistant",
            "content": "\n".join(text_parts).strip(),
        }
        if tool_calls:
            message["tool_calls"] = tool_calls

        return {
            "message": message,
            "usage": usage,
            "finish_reason": first.get("finishReason"),
        }


def build_provider(
    *,
    provider_name: str,
    api_key: str | None,
    base_url: str,
    timeout_seconds: int,
    google_api_key: str | None = None,
    google_base_url: str = "https://generativelanguage.googleapis.com/v1beta",
) -> LLMProvider:
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

    if normalized == "google":
        if not google_api_key:
            raise LLMConfigurationError("Provider is google but GOOGLE_API_KEY is not configured")
        return GoogleProvider(
            api_key=google_api_key,
            base_url=google_base_url,
            timeout_seconds=timeout_seconds,
        )

    raise LLMConfigurationError(f"Unsupported LLM provider '{provider_name}'")
