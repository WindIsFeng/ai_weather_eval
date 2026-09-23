from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from ai_weather_eval.cli import main


FIXTURE = Path(__file__).parents[1] / "fixtures" / "experiment.yaml"


class CliTests(unittest.TestCase):
    def test_config_check(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["config", "check", "--config", str(FIXTURE)])
        self.assertEqual(exit_code, 0)
        self.assertIn("Configuration is valid", output.getvalue())

    def test_catalog_plan_writes_forecast_cases(self) -> None:
        with TemporaryDirectory() as directory:
            cases_file = Path(directory) / "cases.csv"
            output_file = Path(directory) / "forecast_cases.csv"
            pd.DataFrame(
                [
                    {
                        "case_id": "TEST-CE01",
                        "storm_id": "TEST",
                        "reference_event": "landfall",
                        "reference_time": "2022-01-02T03:00:00Z",
                        "latitude": 10.0,
                        "longitude": 120.0,
                    },
                    {
                        "case_id": "TEST-CE02",
                        "storm_id": "TEST",
                        "reference_event": "coastal_approach",
                        "reference_time": "2022-01-02 04:30:00.123456+00:00",
                        "latitude": 11.0,
                        "longitude": 121.0,
                    },
                ]
            ).to_csv(cases_file, index=False)
            with redirect_stdout(StringIO()):
                exit_code = main(
                    [
                        "catalog",
                        "plan",
                        "--config",
                        str(FIXTURE),
                        "--cases-file",
                        str(cases_file),
                        "--output",
                        str(output_file),
                    ]
                )
            self.assertEqual(exit_code, 0)
            schedule = pd.read_csv(output_file)
            self.assertEqual(len(schedule), 10)
            self.assertEqual(schedule.loc[0, "forecast_case_id"], "TEST-CE01-L024")
            self.assertEqual(schedule.loc[0, "actual_lead_hours"], 27.0)


if __name__ == "__main__":
    unittest.main()
