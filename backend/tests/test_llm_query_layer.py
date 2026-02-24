from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import date, datetime, timezone

from app.llm import query_layer
from app.schemas import schemas


@dataclass
class FakeSession:
    id: int
    date: date
    start_time: datetime | None
    type: str
    duration_minutes: int
    moving_duration_minutes: int | None = None
    elapsed_duration_minutes: int | None = None
    distance_km: float | None = None
    elevation_gain_m: int | None = None
    average_pace_min_per_km: float | None = None
    average_heart_rate_bpm: float | None = None
    max_heart_rate_bpm: float | None = None
    perceived_intensity: int | None = None
    notes: str | None = None
    external_id: str | None = None


@dataclass
class FakeDayNote:
    date: date
    note: str


@dataclass
class FakeWeeklyPlan:
    year: int
    week_number: int
    description: str
    target_distance_km: float | None = None
    target_sessions: int | None = None
    tags: str | None = None


class TestTrainingDataQueryService(unittest.TestCase):
    def setUp(self):
        self._orig_sessions = query_layer.crud.get_sessions_by_date_range
        self._orig_notes = query_layer.crud.get_day_notes_by_date_range
        self._orig_plan = query_layer.crud.get_weekly_plan

        self.dataset_sessions = [
            FakeSession(
                id=1,
                date=date(2026, 2, 16),
                start_time=datetime(2026, 2, 16, 6, 30, tzinfo=timezone.utc),
                type="run",
                duration_minutes=95,
                moving_duration_minutes=90,
                elapsed_duration_minutes=95,
                distance_km=19.2,
                elevation_gain_m=240,
                perceived_intensity=7,
                notes="Long aerobic run",
                external_id="manual:1",
            ),
            FakeSession(
                id=2,
                date=date(2026, 2, 18),
                start_time=datetime(2026, 2, 18, 18, 0, tzinfo=timezone.utc),
                type="strength",
                duration_minutes=55,
                moving_duration_minutes=50,
                elapsed_duration_minutes=55,
                perceived_intensity=8,
                notes=None,
                external_id="manual:2",
            ),
            FakeSession(
                id=3,
                date=date(2026, 2, 21),
                start_time=None,
                type="trail",
                duration_minutes=120,
                moving_duration_minutes=108,
                elapsed_duration_minutes=120,
                distance_km=14.0,
                elevation_gain_m=600,
                perceived_intensity=9,
                notes="Hard hills",
                external_id="manual:3",
            ),
        ]

        self.dataset_notes = [
            FakeDayNote(date=date(2026, 2, 16), note="Felt fresh."),
            FakeDayNote(date=date(2026, 2, 21), note="Heavy legs after work stress."),
        ]

        self.dataset_plan = FakeWeeklyPlan(
            year=2026,
            week_number=8,
            description="2 key runs + 1 strength",
            target_distance_km=30.0,
            target_sessions=4,
            tags="base,consistency",
        )

        def fake_get_sessions_by_date_range(_db, start_date, end_date):
            return [
                s for s in self.dataset_sessions if start_date <= s.date <= end_date
            ]

        def fake_get_day_notes_by_date_range(_db, start_date, end_date):
            return [
                n for n in self.dataset_notes if start_date <= n.date <= end_date
            ]

        def fake_get_weekly_plan(_db, year, week_number):
            if year == self.dataset_plan.year and week_number == self.dataset_plan.week_number:
                return self.dataset_plan
            return None

        query_layer.crud.get_sessions_by_date_range = fake_get_sessions_by_date_range
        query_layer.crud.get_day_notes_by_date_range = fake_get_day_notes_by_date_range
        query_layer.crud.get_weekly_plan = fake_get_weekly_plan

    def tearDown(self):
        query_layer.crud.get_sessions_by_date_range = self._orig_sessions
        query_layer.crud.get_day_notes_by_date_range = self._orig_notes
        query_layer.crud.get_weekly_plan = self._orig_plan

    def test_build_context_multi_level_and_salient(self):
        service = query_layer.TrainingDataQueryService(db=None)

        payload = schemas.LLMInterpretRequest(
            query="Summarize this week",
            date_start=date(2026, 2, 16),
            date_end=date(2026, 2, 22),
            levels=[
                schemas.LLMContextLevel.session,
                schemas.LLMContextLevel.day,
                schemas.LLMContextLevel.week,
                schemas.LLMContextLevel.block,
            ],
            include_salient_sessions=True,
            salient_distance_km_threshold=15.0,
            salient_duration_minutes_threshold=90,
            max_sessions_per_level=2,
        )

        context = service.build_context(payload)

        self.assertEqual(context["meta"]["window"]["date_start"], "2026-02-16")
        self.assertEqual(context["meta"]["window"]["date_end"], "2026-02-22")

        session_level = context["levels"]["session"]
        self.assertEqual(session_level["count"], 3)
        self.assertEqual(len(session_level["items"]), 2)

        day_level = context["levels"]["day"]
        self.assertEqual(day_level["count"], 7)
        self.assertTrue(any(item["day_note"] for item in day_level["items"]))

        week_level = context["levels"]["week"]
        self.assertEqual(week_level["count"], 1)
        plan_vs_actual = week_level["items"][0]["plan_vs_actual"]
        self.assertEqual(plan_vs_actual["targets"]["sessions"], 4)
        self.assertEqual(plan_vs_actual["actual"]["sessions"], 3)
        self.assertEqual(plan_vs_actual["delta"]["sessions"], -1)

        block = context["levels"]["block"]
        self.assertIn("weekly_trend", block)
        self.assertGreaterEqual(len(block["weekly_trend"]), 1)

        self.assertGreaterEqual(context["salient_sessions_count"], 2)
        salient_reasons = {
            reason
            for session in context["salient_sessions"]
            for reason in session.get("salient_reasons", [])
        }
        self.assertIn("has_note", salient_reasons)
        self.assertIn("long_duration", salient_reasons)


if __name__ == "__main__":
    unittest.main()
