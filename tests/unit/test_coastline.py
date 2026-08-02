import unittest

from shapely.geometry import Polygon

from ai_weather_eval.catalog.coastline import CoastlineIndex


class CoastlineIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.coastline = CoastlineIndex.from_segments(
            [(0.0, -10.0, 0.0, 10.0)], sample_spacing_km=5.0
        )
        land = Polygon([(0.0, -10.0), (10.0, -10.0), (10.0, 10.0), (0.0, 10.0)])
        self.land_coastline = CoastlineIndex.from_segments(
            [
                (0.0, -10.0, 10.0, -10.0),
                (10.0, -10.0, 10.0, 10.0),
                (10.0, 10.0, 0.0, 10.0),
                (0.0, 10.0, 0.0, -10.0),
            ],
            sample_spacing_km=5.0,
            land_geometries=[land],
        )

    def test_distance_uses_wgs84_geodesic(self) -> None:
        result = self.coastline.distance_to_point(0.0, 1.0)
        self.assertAlmostEqual(result.distance_km, 111.319, places=3)
        self.assertAlmostEqual(result.coast_latitude, 0.0, places=6)
        self.assertAlmostEqual(result.coast_longitude, 0.0, places=6)

    def test_track_crossing_is_found(self) -> None:
        crossings = self.coastline.find_crossings([0.0, 0.0], [-1.0, 1.0])
        self.assertEqual(len(crossings), 1)
        self.assertAlmostEqual(crossings[0].fraction, 0.5, places=6)
        self.assertAlmostEqual(crossings[0].longitude, 0.0, places=6)

    def test_non_crossing_track_is_empty(self) -> None:
        crossings = self.coastline.find_crossings([0.0, 1.0], [1.0, 1.0])
        self.assertEqual(crossings, [])

    def test_land_sea_classification_and_crossing_direction(self) -> None:
        self.assertEqual(
            self.land_coastline.points_on_land([0.0, 0.0], [-1.0, 1.0]).tolist(),
            [False, True],
        )
        landfall = self.land_coastline.find_crossings([0.0, 0.0], [-1.0, 1.0])
        exit_crossing = self.land_coastline.find_crossings([0.0, 0.0], [1.0, -1.0])
        self.assertEqual([crossing.crossing_type for crossing in landfall], ["landfall"])
        self.assertEqual(
            [crossing.crossing_type for crossing in exit_crossing], ["exit"]
        )


if __name__ == "__main__":
    unittest.main()
