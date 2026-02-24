from __future__ import annotations

import unittest

from fastapi import HTTPException

from app.api import api
from app.llm.providers import EchoProvider, LLMConfigurationError, build_provider
from app.schemas import schemas


class TestLLMProvidersAndAPI(unittest.TestCase):
    def test_build_provider_configuration_guards(self):
        with self.assertRaises(LLMConfigurationError):
            build_provider(
                provider_name="mistral",
                api_key=None,
                base_url="https://api.mistral.ai/v1",
                timeout_seconds=10,
            )

        with self.assertRaises(LLMConfigurationError):
            build_provider(
                provider_name="unknown-provider",
                api_key="x",
                base_url="https://example.com",
                timeout_seconds=10,
            )

    def test_echo_provider_round_trip(self):
        provider = EchoProvider()
        text, usage = provider.complete(
            messages=[
                {"role": "system", "content": "ignore"},
                {"role": "user", "content": "hello"},
                {"role": "user", "content": "world"},
            ],
            model="any",
            temperature=0.0,
            max_tokens=100,
        )
        self.assertIn("hello", text)
        self.assertIn("world", text)
        self.assertEqual(usage, {})

    def test_api_llm_interpret_endpoint_calls_service(self):
        original_cls = api.TrainingOSLLMService

        class FakeService:
            def __init__(self, db):
                self.db = db

            def interpret(self, _payload):
                return schemas.LLMInterpretResponse(
                    answer="ok",
                    context={"meta": {}},
                    audit=schemas.LLMAuditResponse(
                        generated_at_utc=schemas.datetime.now(),
                        provider="echo",
                        model="test",
                        deterministic=True,
                        levels=["week"],
                        prompt_generic_key="weekly_analysis_v1.txt",
                        prompt_generic_path="prompts/generic/weekly_analysis_v1.txt",
                    ),
                )

        api.TrainingOSLLMService = FakeService

        try:
            payload = schemas.LLMInterpretRequest(
                query="test",
                levels=[schemas.LLMContextLevel.week],
                provider="echo",
            )
            response = api.interpret_training_data_with_llm(payload=payload, db=None)
            self.assertEqual(response.answer, "ok")
            self.assertEqual(response.audit.provider, "echo")
        finally:
            api.TrainingOSLLMService = original_cls

    def test_api_maps_configuration_error_to_http_400(self):
        original_cls = api.TrainingOSLLMService

        class FakeService:
            def __init__(self, db):
                self.db = db

            def interpret(self, _payload):
                raise api.LLMConfigurationError("bad config")

        api.TrainingOSLLMService = FakeService

        try:
            payload = schemas.LLMInterpretRequest(query="test", provider="mistral")
            with self.assertRaises(HTTPException) as exc_info:
                api.interpret_training_data_with_llm(payload=payload, db=None)
            self.assertEqual(exc_info.exception.status_code, 400)
        finally:
            api.TrainingOSLLMService = original_cls


if __name__ == "__main__":
    unittest.main()
