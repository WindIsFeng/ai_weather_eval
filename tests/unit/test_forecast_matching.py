import unittest

import pandas as pd

from ai_weather_eval.preprocessing.forecast_matching import build_forecast_case_schedule


class ForecastMatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cases = pd.DataFrame(
            [
                {
                    "case_id": "A-CE01",
                    "storm_id": "A",
                    "reference_event": "landfall",
                    "reference_time": "2022-01-02T03:00:00Z",
                    "latitude": 10.0,
                    "longitude": 120.0,
                },
                {
                    "case_id": "B-CE01",
                    "storm_id": "B",
                    "reference_event": "coastal_approach",
                    "reference_time": "2022-01-02T04:30:00Z",
                    "latitude": -10.0,
                    "longitude": 150.0,
                },
            ]
        )

    def test_both_event_types_use_their_reference_time(self) -> None:
        schedule = build_forecast_case_schedule(
            self.cases,
            nominal_lead_hours=[24],
            standard_cycle_hours_utc=[0, 6, 12, 18],
            maximum_cycle_offset_hours=3,
        )
        self.assertEqual(len(schedule), 2)
        self.assertEqual(schedule.loc[0, "forecast_case_id"], "A-CE01-L024")
        self.assertEqual(schedule.loc[0, "init_time"], pd.Timestamp("2022-01-01T00:00:00Z"))
        self.assertEqual(schedule.loc[0, "actual_lead_hours"], 27.0)
        self.assertEqual(schedule.loc[0, "cycle_offset_hours"], -3.0)
        self.assertEqual(schedule.loc[1, "init_time"], pd.Timestamp("2022-01-01T06:00:00Z"))
        self.assertEqual(schedule.loc[1, "actual_lead_hours"], 22.5)

    def test_unmatched_cycle_is_visible(self) -> None:
        schedule = build_forecast_case_schedule(
            self.cases.iloc[:1],
            nominal_lead_hours=[24],
            standard_cycle_hours_utc=[0, 6, 12, 18],
            maximum_cycle_offset_hours=2,
        )
        self.assertEqual(schedule.loc[0, "schedule_status"], "no_cycle_within_tolerance")
        self.assertTrue(pd.isna(schedule.loc[0, "init_time"]))


if __name__ == "__main__":
    unittest.main()
