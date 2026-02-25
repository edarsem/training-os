from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session as DBSession

from app.core.config import settings
from app.llm.mcp_tools import execute_mcp_tool, get_mcp_tools_schema
from app.llm.prompt_loader import PromptRepository
from app.llm.providers import LLMConfigurationError, LLMProviderError, build_provider
from app.llm.query_layer import TrainingDataQueryService
from app.schemas import schemas


class TrainingOSLLMService:
    def __init__(self, db: DBSession):
        self.db = db

    def interpret(self, request: schemas.LLMInterpretRequest) -> schemas.LLMInterpretResponse:
        prompt_repo = PromptRepository(settings.BASE_DIR / "prompts")
        language = (request.language or settings.LLM_USER_LANGUAGE or settings.LLM_DEFAULT_LANGUAGE).strip().lower()
        default_language = (settings.LLM_DEFAULT_LANGUAGE or "en").strip().lower()

        generic_candidates: list[str]
        if request.generic_prompt_key:
            generic_candidates = [request.generic_prompt_key]
        else:
            base = settings.LLM_GENERIC_PROMPT_BASENAME
            generic_candidates = [
                f"{base}.{language}",
                f"{base}.{default_language}",
                base,
            ]

        private_candidates: list[str] = []
        if request.private_prompt_key:
            private_candidates.extend(
                [
                    f"{request.private_prompt_key}.{language}",
                    f"{request.private_prompt_key}.{default_language}",
                    request.private_prompt_key,
                ]
            )
        elif settings.LLM_PRIVATE_PROMPT_BASENAME:
            base = settings.LLM_PRIVATE_PROMPT_BASENAME
            private_candidates.extend(
                [
                    f"{base}.{language}",
                    f"{base}.{default_language}",
                    base,
                ]
            )

        template_base = settings.LLM_PRIVATE_TEMPLATE_BASENAME
        if template_base:
            private_candidates.extend(
                [
                    f"{template_base}.{language}",
                    f"{template_base}.{default_language}",
                    template_base,
                ]
            )

        prompt_bundle = prompt_repo.resolve_from_candidates(
            generic_candidates=generic_candidates,
            private_candidates=private_candidates,
        )

        system_prompt_parts = [
            (
                "You are the Training OS analysis assistant. "
                "You must interpret the provided structured context only. "
                "Do not invent missing facts. "
                "If information is missing, say it explicitly. "
                "Focus on concise coaching insights and deterministic reasoning."
            ),
            prompt_bundle.generic_text,
        ]

        if prompt_bundle.private_text:
            system_prompt_parts.append(prompt_bundle.private_text)

        system_prompt = "\n\n".join(part.strip() for part in system_prompt_parts if part and part.strip())

        provider_name = request.provider or settings.LLM_PROVIDER
        model_name = request.model or settings.MISTRAL_MODEL
        temperature = 0.0 if request.deterministic else settings.LLM_TEMPERATURE

        provider = build_provider(
            provider_name=provider_name,
            api_key=settings.MISTRAL_API_KEY,
            base_url=settings.MISTRAL_API_BASE_URL,
            timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
        )

        mcp_enabled = bool(settings.LLM_MCP_ENABLED) and provider_name.lower() != "echo"
        if mcp_enabled:
            return self._interpret_with_mcp(
                request=request,
                provider=provider,
                provider_name=provider_name,
                model_name=model_name,
                temperature=temperature,
                language=language,
                system_prompt=system_prompt,
                prompt_bundle=prompt_bundle,
            )

        return self._interpret_legacy(
            request=request,
            provider=provider,
            provider_name=provider_name,
            model_name=model_name,
            temperature=temperature,
            language=language,
            system_prompt=system_prompt,
            prompt_bundle=prompt_bundle,
        )

    def _interpret_legacy(
        self,
        *,
        request: schemas.LLMInterpretRequest,
        provider: Any,
        provider_name: str,
        model_name: str,
        temperature: float,
        language: str,
        system_prompt: str,
        prompt_bundle: Any,
    ) -> schemas.LLMInterpretResponse:
        context_builder = TrainingDataQueryService(self.db)
        context = context_builder.build_context(request)

        user_payload = {
            "question": request.query,
            "context": context,
            "tool_hints": request.tool_hints,
            "constraints": {
                "interpret_not_compute": True,
                "max_bullets": 8,
            },
        }
        user_message_content = json.dumps(user_payload, ensure_ascii=False, sort_keys=True)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message_content},
        ]

        answer, usage = provider.complete(
            messages=messages,
            model=model_name,
            temperature=temperature,
            max_tokens=settings.LLM_MAX_TOKENS,
        )

        audit = schemas.LLMAuditResponse(
            generated_at_utc=datetime.now(timezone.utc),
            provider=provider_name,
            model=model_name,
            language=language,
            deterministic=request.deterministic,
            levels=[level.value for level in request.levels],
            window=context.get("meta", {}).get("window", {}),
            prompt_generic_key=prompt_bundle.generic_key,
            prompt_generic_path=prompt_bundle.generic_path,
            prompt_private_key=prompt_bundle.private_key,
            prompt_private_path=prompt_bundle.private_path,
            tool_hints=request.tool_hints,
            usage=usage if isinstance(usage, dict) else {},
        )

        return schemas.LLMInterpretResponse(
            answer=answer,
            context=context if request.include_context_in_response else None,
            input_preview=(
                schemas.LLMInputPreview(
                    system_prompt=system_prompt,
                    user_message=user_message_content,
                    messages=messages,
                )
                if request.include_input_preview
                else None
            ),
            audit=audit,
        )

    def _interpret_with_mcp(
        self,
        *,
        request: schemas.LLMInterpretRequest,
        provider: Any,
        provider_name: str,
        model_name: str,
        temperature: float,
        language: str,
        system_prompt: str,
        prompt_bundle: Any,
    ) -> schemas.LLMInterpretResponse:
        now_iso = datetime.now(timezone.utc).date().isoformat()
        user_payload = {
            "current_utc_date": now_iso,
            "question": request.query,
            "now_iso_date": now_iso,
            "locale": language,
            "fallback_anchor_year": request.anchor_year,
            "fallback_anchor_week": request.anchor_week,
            "instruction": (
                "Use tools to fetch only the minimum data required to answer accurately. "
                "Prefer calling data tools directly with explicit ISO dates/ranges whenever you can infer them from the user query. "
                "Use temporal_ref only when ISO values are not explicit or are relative/ambiguous (e.g., last monday, last month). "
                "For comparisons like 'janvier vs juillet', call get_block_summary for each explicit month range directly; do not call resolve_time_reference first."
            ),
        }
        user_message_content = json.dumps(user_payload, ensure_ascii=False)

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    f"{system_prompt}\n\n"
                    f"Current UTC date anchor: {now_iso}.\n"
                    "MCP mode is enabled. You MUST use tools when data is needed. "
                    "Do not claim missing data before trying appropriate tools. "
                    "Prefer minimal tool calls and minimal data volume."
                ),
            },
            {"role": "user", "content": user_message_content},
        ]

        tools = get_mcp_tools_schema()
        mcp_trace: list[dict[str, Any]] = []
        usage_aggregate: dict[str, Any] = {}
        answer = ""
        max_calls = int(settings.LLM_MCP_MAX_TOOL_CALLS)

        for _ in range(max_calls + 1):
            response = provider.complete_with_tools(
                messages=messages,
                tools=tools,
                model=model_name,
                temperature=temperature,
                max_tokens=settings.LLM_MAX_TOKENS,
            )

            usage = response.get("usage", {}) if isinstance(response, dict) else {}
            if isinstance(usage, dict):
                for key, value in usage.items():
                    if isinstance(value, (int, float)):
                        usage_aggregate[key] = usage_aggregate.get(key, 0) + value

            message = response.get("message", {}) if isinstance(response, dict) else {}
            tool_calls = message.get("tool_calls") or []

            assistant_content = message.get("content")
            if isinstance(assistant_content, list):
                assistant_content = "\n".join(
                    part.get("text", "") for part in assistant_content if isinstance(part, dict)
                ).strip()
            assistant_content = str(assistant_content or "").strip()

            if tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": assistant_content,
                        "tool_calls": tool_calls,
                    }
                )

                for call in tool_calls:
                    function = call.get("function", {}) if isinstance(call, dict) else {}
                    name = function.get("name")
                    args_raw = function.get("arguments", "{}")
                    call_id = call.get("id") if isinstance(call, dict) else None

                    try:
                        arguments = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
                    except json.JSONDecodeError:
                        arguments = {}

                    tool_result = execute_mcp_tool(
                        self.db,
                        name=name,
                        arguments=arguments,
                        time_resolver=lambda query, now_iso_date, lang: self._resolve_time_reference_with_llm(
                            provider=provider,
                            model_name=model_name,
                            query=query,
                            now_iso_date=now_iso_date,
                            language=lang or language,
                        ),
                    )
                    mcp_trace.append(
                        {
                            "type": "tool_call",
                            "name": name,
                            "arguments": arguments,
                            "result_preview": tool_result,
                        }
                    )

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": name,
                            "content": json.dumps(tool_result, ensure_ascii=False),
                        }
                    )

                continue

            answer = assistant_content
            mcp_trace.append({"type": "final_answer", "content": answer})
            break

        if not answer:
            answer = "I could not complete the MCP tool workflow for this request."

        context_payload = {
            "mcp_trace": mcp_trace,
        } if request.include_context_in_response else None

        audit = schemas.LLMAuditResponse(
            generated_at_utc=datetime.now(timezone.utc),
            provider=provider_name,
            model=model_name,
            language=language,
            deterministic=request.deterministic,
            levels=["mcp"],
            window={},
            prompt_generic_key=prompt_bundle.generic_key,
            prompt_generic_path=prompt_bundle.generic_path,
            prompt_private_key=prompt_bundle.private_key,
            prompt_private_path=prompt_bundle.private_path,
            tool_hints=request.tool_hints,
            usage=usage_aggregate,
        )

        return schemas.LLMInterpretResponse(
            answer=answer,
            context=context_payload,
            input_preview=(
                schemas.LLMInputPreview(
                    system_prompt=messages[0].get("content", ""),
                    user_message=user_message_content,
                    messages=messages,
                )
                if request.include_input_preview
                else None
            ),
            mcp_trace=mcp_trace,
            audit=audit,
        )

    def _resolve_time_reference_with_llm(
        self,
        *,
        provider: Any,
        model_name: str,
        query: str,
        now_iso_date: str,
        language: str,
    ) -> dict[str, Any]:
        system_prompt = (
            "You resolve temporal expressions into either a single date or an explicit date range. "
            "Return strict JSON only with keys: mode, reference_date_iso, range_start_iso, range_end_iso, label. "
            "mode must be exactly one of: date, range. "
            "For mode=date, provide reference_date_iso (YYYY-MM-DD). "
            "For mode=range, provide range_start_iso and range_end_iso (YYYY-MM-DD)."
        )
        user_prompt = json.dumps(
            {
                "query": query,
                "now_iso_date": now_iso_date,
                "language": language,
                "calendar_hint": "Use ISO calendar logic when query refers to weeks.",
            },
            ensure_ascii=False,
            sort_keys=True,
        )

        text, _usage = provider.complete(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=model_name,
            temperature=0.0,
            max_tokens=300,
        )

        try:
            data = json.loads(text)
            mode = str(data.get("mode") or "date").strip().lower()

            if mode == "range":
                start_iso = str(data.get("range_start_iso") or now_iso_date)
                end_iso = str(data.get("range_end_iso") or start_iso)
                start_date = datetime.fromisoformat(start_iso).date()
                end_date = datetime.fromisoformat(end_iso).date()

                if end_date < start_date:
                    start_date, end_date = end_date, start_date

                return {
                    "mode": "range",
                    "range_start_iso": start_date.isoformat(),
                    "range_end_iso": end_date.isoformat(),
                    "label": data.get("label") or query,
                }

            ref = str(data.get("reference_date_iso") or now_iso_date)
            datetime.fromisoformat(ref)
            return {
                "mode": "date",
                "reference_date_iso": ref,
                "label": data.get("label") or query,
            }
        except Exception:
            return {
                "mode": "date",
                "reference_date_iso": now_iso_date,
                "label": query,
            }


__all__ = [
    "TrainingOSLLMService",
    "LLMConfigurationError",
    "LLMProviderError",
]
