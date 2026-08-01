import unittest

from ai_weather_eval.adapters.base import validate_canonical_dataset
from ai_weather_eval.metrics.track import great_circle_distance_km
from ai_weather_eval.schemas import CANONICAL_FIELD_VARIABLES


class _DatasetStub:
    dims = ("init_time", "lead_time", "latitude", "longitude")
    data_vars = CANONICAL_FIELD_VARIABLES


class ContractTests(unittest.TestCase):
    def test_canonical_dataset_stub(self) -> None:
        validate_canonical_dataset(_DatasetStub())

    def test_zero_track_distance(self) -> None:
        self.assertEqual(great_circle_distance_km(10.0, 120.0, 10.0, 120.0), 0.0)

    def test_one_degree_equatorial_distance(self) -> None:
        self.assertAlmostEqual(great_circle_distance_km(0.0, 0.0, 0.0, 1.0), 111.195, places=3)


if __name__ == "__main__":
    unittest.main()

