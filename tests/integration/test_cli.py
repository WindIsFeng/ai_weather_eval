from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import unittest

from ai_weather_eval.cli import main


FIXTURE = Path(__file__).parents[1] / "fixtures" / "experiment.yaml"


class CliTests(unittest.TestCase):
    def test_config_check(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["config", "check", "--config", str(FIXTURE)])
        self.assertEqual(exit_code, 0)
        self.assertIn("Configuration is valid", output.getvalue())


if __name__ == "__main__":
    unittest.main()

