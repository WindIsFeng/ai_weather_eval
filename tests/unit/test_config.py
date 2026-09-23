from pathlib import Path
import unittest

from ai_weather_eval.config import ConfigError, load_yaml, validate_experiment


FIXTURE = Path(__file__).parents[1] / "fixtures" / "experiment.yaml"


class ConfigTests(unittest.TestCase):
    def test_fixture_is_valid(self) -> None:
        config = load_yaml(FIXTURE)
        validate_experiment(config)

    def test_unsorted_leads_are_rejected(self) -> None:
        config = load_yaml(FIXTURE)
        config["forecast_sampling"]["nominal_lead_hours"] = [48, 24]
        with self.assertRaises(ConfigError):
            validate_experiment(config)

    def test_missing_cycles_are_rejected(self) -> None:
        config = load_yaml(FIXTURE)
        del config["forecast_sampling"]["standard_cycle_hours_utc"]
        with self.assertRaises(ConfigError):
            validate_experiment(config)


if __name__ == "__main__":
    unittest.main()
