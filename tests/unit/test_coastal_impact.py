import unittest

import pandas as pd

from ai_weather_eval.catalog.coastal_impact import (
    CoastalImpactSelection,
    build_coastal_impact_catalog,
)
from ai_weather_eval.catalog.coastline import CoastlineIndex


def _storm_frame(*, radius_nmile: float | None = 100.0) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "SID": ["TEST", "TEST"],
            "NAME": ["TEST", "TEST"],
            "BASIN": ["WP", "WP"],
            "NATURE": ["TS", "TS"],
            "TRACK_TYPE": ["main", "main"],
            "TRACK_TYPE_NORMALIZED": ["main", "main"],
            "USA_STATUS": ["TY", "TY"],
            "ISO_TIME": pd.to_datetime(
                ["2022-01-01T00:00:00Z", "2022-01-01T03:00:00Z"], utc=True
            ),
            "LAT": [0.0, 0.0],
            "LON": [-1.0, 1.0],
            "USA_WIND": [70.0, 70.0],
            "USA_PRES": [980.0, 980.0],
            "SOURCE_ROW": [0, 1],
        }
    )
    for threshold in (34, 50, 64):
        for quadrant in ("NE", "SE", "SW", "NW"):
            frame[f"USA_R{threshold}_{quadrant}"] = radius_nmile
    return frame


class CoastalImpactCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.coastline = CoastlineIndex.from_segments(
            [(0.0, -10.0, 0.0, 10.0)], sample_spacing_km=5.0
        )
        self.selection = CoastalImpactSelection(
            start=pd.Timestamp("2022-01-01T00:00:00Z"),
            end=pd.Timestamp("2022-12-31T23:59:59Z"),
        )

    def test_crossing_strong_storm_is_primary_tier_a(self) -> None:
        cases, points = build_coastal_impact_catalog(
            _storm_frame(), self.coastline, self.selection
        )
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases.loc[0, "tier"], "A")
        self.assertTrue(cases.loc[0, "primary_sample"])
        self.assertTrue(cases.loc[0, "coastline_crossing"])
        self.assertEqual(cases.loc[0, "minimum_coast_distance_km"], 0.0)
        self.assertFalse(points.empty)

    def test_missing_radii_use_distance_proxy(self) -> None:
        cases, _ = build_coastal_impact_catalog(
            _storm_frame(radius_nmile=None), self.coastline, self.selection
        )
        self.assertEqual(len(cases), 1)
        self.assertIn("distance_proxy", cases.loc[0, "selection_method"])

    def test_coastal_intensity_threshold_is_enforced(self) -> None:
        storm = _storm_frame()
        storm["USA_WIND"] = 60.0
        cases, _ = build_coastal_impact_catalog(storm, self.coastline, self.selection)
        self.assertTrue(cases.empty)

if __name__ == "__main__":
    unittest.main()
