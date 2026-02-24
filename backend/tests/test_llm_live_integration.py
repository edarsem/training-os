from __future__ import annotations

import unittest

from app.core.config import settings
from app.main import app

try:
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover
    TestClient = None


class TestLLMLiveIntegration(unittest.TestCase):
    @unittest.skipIf(TestClient is None, "fastapi TestClient is unavailable")
    def test_post_llm_interpret_real_mistral(self):
        if not settings.MISTRAL_API_KEY:
            self.skipTest("MISTRAL_API_KEY is not configured")

        client = TestClient(app)

        payload = {
            "query": "Give a concise summary of this week based only on provided context.",
            "levels": ["week", "session"],
            "provider": "mistral",
            "deterministic": True,
            "include_context_in_response": False,
            "generic_prompt_key": "weekly_analysis_v1.txt",
            "max_sessions_per_level": 15,
            "include_salient_sessions": True,
        }

        response = client.post("/api/llm/interpret", json=payload)

        self.assertEqual(
            response.status_code,
            200,
            msg=f"Expected 200 but got {response.status_code}: {response.text}",
        )

        body = response.json()
        self.assertTrue(isinstance(body.get("answer"), str))
        self.assertTrue(len(body.get("answer", "").strip()) > 0)

        audit = body.get("audit", {})
        self.assertEqual(audit.get("provider"), "mistral")
        self.assertIn("week", audit.get("levels", []))
        self.assertEqual(audit.get("prompt_generic_key"), "weekly_analysis_v1.txt")


if __name__ == "__main__":
    unittest.main()
