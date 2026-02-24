from __future__ import annotations

import unittest

from app.llm import service as llm_service
from app.schemas import schemas


class TestTrainingOSLLMService(unittest.TestCase):
    def setUp(self):
        self._orig_build_context = llm_service.TrainingDataQueryService.build_context
        self._orig_default_language = llm_service.settings.LLM_DEFAULT_LANGUAGE
        self._orig_user_language = llm_service.settings.LLM_USER_LANGUAGE
        self._orig_generic_base = llm_service.settings.LLM_GENERIC_PROMPT_BASENAME
        self._orig_private_base = llm_service.settings.LLM_PRIVATE_PROMPT_BASENAME
        self._orig_private_template = llm_service.settings.LLM_PRIVATE_TEMPLATE_BASENAME

    def tearDown(self):
        llm_service.TrainingDataQueryService.build_context = self._orig_build_context
        llm_service.settings.LLM_DEFAULT_LANGUAGE = self._orig_default_language
        llm_service.settings.LLM_USER_LANGUAGE = self._orig_user_language
        llm_service.settings.LLM_GENERIC_PROMPT_BASENAME = self._orig_generic_base
        llm_service.settings.LLM_PRIVATE_PROMPT_BASENAME = self._orig_private_base
        llm_service.settings.LLM_PRIVATE_TEMPLATE_BASENAME = self._orig_private_template

    def test_interpret_with_echo_provider_returns_audit_and_context(self):
        def fake_build_context(_self, _request):
            return {
                "meta": {
                    "window": {
                        "date_start": "2026-02-16",
                        "date_end": "2026-02-22",
                        "anchor_year": 2026,
                        "anchor_week": 8,
                    },
                    "levels": ["week"],
                },
                "levels": {"week": {"count": 1, "items": []}},
                "salient_sessions": [],
                "salient_sessions_count": 0,
            }

        llm_service.TrainingDataQueryService.build_context = fake_build_context

        payload = schemas.LLMInterpretRequest(
            query="What happened this week?",
            levels=[schemas.LLMContextLevel.week],
            provider="echo",
            include_context_in_response=True,
            generic_prompt_key="system_base.en.txt",
        )

        service = llm_service.TrainingOSLLMService(db=None)
        response = service.interpret(payload)

        self.assertIsNotNone(response.answer)
        self.assertIsNotNone(response.context)
        self.assertEqual(response.audit.provider, "echo")
        self.assertEqual(response.audit.levels, ["week"])
        self.assertEqual(response.audit.prompt_generic_key, "system_base.en.txt")
        self.assertEqual(response.audit.language, "en")

    def test_missing_prompt_raises_file_not_found(self):
        def fake_build_context(_self, _request):
            return {
                "meta": {"window": {}, "levels": ["week"]},
                "levels": {},
                "salient_sessions": [],
                "salient_sessions_count": 0,
            }

        llm_service.TrainingDataQueryService.build_context = fake_build_context

        payload = schemas.LLMInterpretRequest(
            query="test",
            levels=[schemas.LLMContextLevel.week],
            provider="echo",
            generic_prompt_key="does_not_exist",
        )

        service = llm_service.TrainingOSLLMService(db=None)
        with self.assertRaises(FileNotFoundError):
            service.interpret(payload)

    def test_default_language_and_private_prompt_fallback_chain(self):
        def fake_build_context(_self, _request):
            return {
                "meta": {"window": {}, "levels": ["week"]},
                "levels": {},
                "salient_sessions": [],
                "salient_sessions_count": 0,
            }

        llm_service.TrainingDataQueryService.build_context = fake_build_context
        llm_service.settings.LLM_DEFAULT_LANGUAGE = "en"
        llm_service.settings.LLM_USER_LANGUAGE = "fr"
        llm_service.settings.LLM_GENERIC_PROMPT_BASENAME = "system_base"
        llm_service.settings.LLM_PRIVATE_PROMPT_BASENAME = "my_profile"
        llm_service.settings.LLM_PRIVATE_TEMPLATE_BASENAME = "profile"

        payload = schemas.LLMInterpretRequest(
            query="test defaults",
            levels=[schemas.LLMContextLevel.week],
            provider="echo",
        )

        service = llm_service.TrainingOSLLMService(db=None)
        response = service.interpret(payload)

        self.assertEqual(response.audit.language, "fr")
        self.assertEqual(response.audit.prompt_generic_key, "system_base.fr")
        self.assertEqual(response.audit.prompt_private_key, "my_profile.fr")


if __name__ == "__main__":
    unittest.main()
