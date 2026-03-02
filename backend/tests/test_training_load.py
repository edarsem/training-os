from __future__ import annotations

import unittest
from datetime import date

from app.models import models
from app.training_load import TrainingLoadConfig, compute_training_load_series


class TestTrainingLoadComputation(unittest.TestCase):
    def test_daily_load_and_state_updates(self):
        config = TrainingLoadConfig(
            threshold_hr=176,
            zone_coefficients=[1, 2, 3, 4, 5, 6],
            atl_time_constant_days=7,
            ctl_time_constant_days=42,
        )

        sessions = [
            models.Session(
                id=1,
                date=date(2026, 2, 1),
                type="run",
                duration_minutes=60,
                moving_duration_minutes=60,
                average_heart_rate_bpm=160.0,
            ),
            models.Session(
                id=2,
                date=date(2026, 2, 2),
                type="run",
                duration_minutes=30,
                moving_duration_minutes=30,
                average_heart_rate_bpm=130.0,
            ),
        ]

        result = compute_training_load_series(
            sessions=sessions,
            session_zone_time_map={},
            start_date=date(2026, 2, 1),
            end_date=date(2026, 2, 2),
            config=config,
        )

        day1 = result["daily"][0]
        day2 = result["daily"][1]

        # 160/176 ~= 90.9 -> zone 3 -> coefficient 3
        self.assertEqual(day1["load"], 180.0)
        # 130/176 ~= 73.9 -> zone 1 -> coefficient 1
        self.assertEqual(day2["load"], 30.0)

        self.assertGreater(day1["atl"], 0)
        self.assertGreater(day1["ctl"], 0)
        self.assertIsNotNone(day2["acwr"])


if __name__ == "__main__":
    unittest.main()
