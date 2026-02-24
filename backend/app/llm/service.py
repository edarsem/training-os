from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session as DBSession

from app.core.config import settings
from app.llm.prompt_loader import PromptRepository
from app.llm.providers import LLMConfigurationError, LLMProviderError, build_provider
from app.llm.query_layer import TrainingDataQueryService
from app.schemas import schemas


class TrainingOSLLMService:
    def __init__(self, db: DBSession):
        self.db = db

    def interpret(self, request: schemas.LLMInterpretRequest) -> schemas.LLMInterpretResponse:
        context_builder = TrainingDataQueryService(self.db)
        context = context_builder.build_context(request)

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

        provider_name = request.provider or settings.LLM_PROVIDER
        model_name = request.model or settings.MISTRAL_MODEL
        temperature = 0.0 if request.deterministic else settings.LLM_TEMPERATURE

        provider = build_provider(
            provider_name=provider_name,
            api_key=settings.MISTRAL_API_KEY,
            base_url=settings.MISTRAL_API_BASE_URL,
            timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
        )

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


__all__ = [
    "TrainingOSLLMService",
    "LLMConfigurationError",
    "LLMProviderError",
]
