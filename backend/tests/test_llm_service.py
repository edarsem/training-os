from __future__ import annotations

import unittest

from app.llm import service as llm_service
from app.schemas import schemas


class TestTrainingOSLLMService(unittest.TestCase):
    def setUp(self):
        self._orig_build_context = llm_service.TrainingDataQueryService.build_context

    def tearDown(self):
        llm_service.TrainingDataQueryService.build_context = self._orig_build_context

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
            generic_prompt_key="weekly_analysis_v1.txt",
        )

        service = llm_service.TrainingOSLLMService(db=None)
        response = service.interpret(payload)

        self.assertIsNotNone(response.answer)
        self.assertIsNotNone(response.context)
        self.assertEqual(response.audit.provider, "echo")
        self.assertEqual(response.audit.levels, ["week"])
        self.assertEqual(response.audit.prompt_generic_key, "weekly_analysis_v1.txt")

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


if __name__ == "__main__":
    unittest.main()
