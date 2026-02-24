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
        prompt_bundle = prompt_repo.resolve(
            generic_key=request.generic_prompt_key,
            private_key=request.private_prompt_key,
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

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True)},
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
            audit=audit,
        )


__all__ = [
    "TrainingOSLLMService",
    "LLMConfigurationError",
    "LLMProviderError",
]
