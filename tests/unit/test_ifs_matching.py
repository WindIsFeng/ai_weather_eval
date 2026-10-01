"""Scientific matching checks: identity, fixed cycles, time support and duplicates."""

import unittest

import numpy as np
import pandas as pd

from ai_weather_eval.preprocessing.ifs_matching import match_ifs_to_cohort


class IFSMatchingTests(unittest.TestCase):
    def setUp(self):
        self.init = pd.Timestamp("2024-08-01T00:00:00Z")
        self.reference = self.init + pd.Timedelta(hours=27)
        self.events = pd.DataFrame([{
            "case_id": "SID1-CE01", "storm_id": "SID1", "reference_time": self.reference,
            "basin": "NA", "name": "TEST", "reference_event": "landfall",
        }])
        self.cases = pd.DataFrame([{
            "forecast_case_id": "SID1-CE01-L024", "case_id": "SID1-CE01", "storm_id": "SID1",
            "reference_time": self.reference, "init_time": self.init, "nominal_lead_hours": 24,
        }])
        self.messages = pd.DataFrame([self.message("a#1")])
        self.tracks = self.points("a#1")
        self.truth = pd.DataFrame([{
            "SID": "SID1", "USA_ATCF_ID": "AL012024", "ISO_TIME": self.init,
            "TRACK_TYPE": "main", "NATURE": "ET", "LAT": 10., "LON": -50., "NAME": "TEST",
        }])

    def message(self, message_id, init=None, **kwargs):
        return {
            "message_id": message_id, "source_file": message_id.split("#")[0],
            "init_time": self.init if init is None else init, "storm_identifier": "01L",
            "storm_name": "TEST", "member_number": 52,
            "observed_latitude": 10., "observed_longitude": -50.,
            "analysis_latitude": np.nan, "analysis_longitude": np.nan,
            "positive_position_count": 2, "max_valid_lead_hours": 30,
            **kwargs,
        }

    def points(self, message_id, leads=(0, 24, 30), init=None):
        init = self.init if init is None else init
        return pd.DataFrame([{
            "message_id": message_id, "init_time": init, "storm_identifier": "01L",
            "member_number": 52, "lead_hours": lead,
            "valid_time": init + pd.Timedelta(hours=lead),
            "latitude": 10. if lead else np.nan,
            "longitude": -50. if lead else np.nan,
            "pressure_hpa": 980., "max_wind_ms": 30., "r34_ne_km": 100.,
        } for lead in leads])

    def run_match(self):
        return match_ifs_to_cohort(self.messages, self.tracks, self.truth, self.events, self.cases)

    def test_observed_identity_and_six_hour_bracket_keep_et_and_na(self):
        result = self.run_match()
        link = result["storm_links"].iloc[0]
        case = result["case_overlap"].iloc[0]
        self.assertEqual(link.identity_status, "matched_cohort")
        self.assertTrue(link.identifier_agrees)
        self.assertTrue(case.forecast_available)
        self.assertEqual(case.coverage_method, "interpolated")
        self.assertEqual(case.previous_valid_time, self.init + pd.Timedelta(hours=24))
        self.assertEqual(case.next_valid_time, self.init + pd.Timedelta(hours=30))
        self.assertTrue(case.forecast_wind_available)
        self.assertEqual(result["event_overlap"].iloc[0].basin, "NA")
        self.assertEqual(result["coverage_by_year"].iloc[0].available_cases, 1)

    def test_exact_output_and_independent_intensity_missingness(self):
        self.events.loc[0, "reference_time"] = self.init + pd.Timedelta(hours=24)
        self.cases.loc[0, "reference_time"] = self.events.reference_time.iloc[0]
        self.tracks.loc[self.tracks.lead_hours.eq(24), "max_wind_ms"] = np.nan
        case = self.run_match()["case_overlap"].iloc[0]
        self.assertTrue(case.forecast_available)
        self.assertEqual(case.coverage_method, "exact")
        self.assertFalse(case.forecast_wind_available)
        self.assertTrue(case.forecast_pressure_available)

    def test_interpolation_requires_both_intensity_endpoints(self):
        self.tracks.loc[self.tracks.lead_hours.eq(30), "max_wind_ms"] = np.nan
        case = self.run_match()["case_overlap"].iloc[0]
        self.assertTrue(case.forecast_available)
        self.assertFalse(case.forecast_wind_available)

    def test_observation_only_does_not_count_as_a_forecast(self):
        self.tracks = self.points("a#1", (0,))
        self.tracks.loc[0, ["latitude", "longitude"]] = [10., -50.]
        result = self.run_match()
        self.assertEqual(result["storm_links"].iloc[0].positive_position_count_derived, 0)
        self.assertFalse(result["event_overlap"].iloc[0].storm_has_forecast)
        self.assertFalse(result["case_overlap"].iloc[0].forecast_available)
        self.assertEqual(result["case_overlap"].iloc[0].unavailable_reason,
                         "no_positive_position_points")

    def test_missing_forecast_positions_do_not_count_as_positive_points(self):
        self.tracks.loc[self.tracks.lead_hours.gt(0), "latitude"] = np.nan
        result = self.run_match()
        self.assertFalse(result["event_overlap"].iloc[0].storm_has_forecast)
        self.assertFalse(result["case_overlap"].iloc[0].forecast_available)

    def test_long_gap_is_not_event_coverage(self):
        self.tracks = self.points("a#1", (0, 24, 36))
        result = self.run_match()
        self.assertTrue(result["event_overlap"].iloc[0].storm_has_forecast)
        self.assertFalse(result["event_overlap"].iloc[0].event_time_covered)
        self.assertEqual(result["case_overlap"].iloc[0].unavailable_reason,
                         "reference_time_not_covered")

    def test_missing_observed_identity_cannot_use_analysis_or_name(self):
        self.messages.loc[0, "observed_latitude"] = np.nan
        self.messages.loc[0, ["analysis_latitude", "analysis_longitude"]] = [10., -50.]
        result = self.run_match()
        self.assertEqual(result["storm_links"].iloc[0].identity_status, "missing_observed_position")
        self.assertFalse(result["event_overlap"].iloc[0].storm_has_forecast)

    def test_identifier_mismatch_can_match_a_unique_same_time_position(self):
        self.messages.loc[0, "storm_identifier"] = "02L"
        link = self.run_match()["storm_links"].iloc[0]
        self.assertEqual(link.storm_id, "SID1")
        self.assertFalse(link.identifier_agrees)
        self.assertEqual(link.id_mapping_method, "position_identifier_mismatch")

    def test_identifier_collision_does_not_merge_position_distinct_storms(self):
        second = self.message("b#1", observed_latitude=15.)
        self.messages = pd.concat([self.messages, pd.DataFrame([second])], ignore_index=True)
        self.tracks = pd.concat([self.tracks, self.points("b#1")], ignore_index=True)
        rival = self.truth.iloc[0].copy()
        rival["SID"], rival["USA_ATCF_ID"], rival["LAT"] = "SID2", "AL022024", 15.
        self.truth = pd.concat([self.truth, rival.to_frame().T], ignore_index=True)
        links = self.run_match()["storm_links"]
        self.assertEqual(links.storm_id.tolist(), ["SID1", "SID2"])
        self.assertEqual(links.duplicate_status.tolist(), ["unique", "unique"])
        self.assertFalse(links.iloc[1].identifier_agrees)

    def test_southern_season_year_is_not_assumed_to_equal_calendar_year(self):
        self.messages.loc[0, "storm_identifier"] = "01S"
        self.truth.loc[0, "USA_ATCF_ID"] = "SH012025"
        link = self.run_match()["storm_links"].iloc[0]
        self.assertTrue(link.identifier_agrees)
        self.assertEqual(link.storm_id, "SID1")

    def test_close_competing_storms_are_ambiguous_despite_matching_identifier(self):
        rival = self.truth.iloc[0].copy()
        rival["SID"], rival["USA_ATCF_ID"], rival["LAT"] = "SID2", "AL022024", 10.1
        self.truth = pd.concat([self.truth, rival.to_frame().T], ignore_index=True)
        result = self.run_match()
        self.assertEqual(result["storm_links"].iloc[0].identity_status, "ambiguous_position")
        self.assertFalse(result["case_overlap"].iloc[0].forecast_available)

    def test_position_more_than_150_km_away_does_not_match_the_same_name(self):
        self.messages.loc[0, "observed_latitude"] = 12.
        link = self.run_match()["storm_links"].iloc[0]
        self.assertEqual(link.identity_status, "no_position_match")
        self.assertEqual(link.storm_id, "")

    def test_outside_cohort_storm_is_distinguished(self):
        self.truth.loc[0, "SID"] = "OTHER"
        result = self.run_match()
        self.assertEqual(result["storm_links"].iloc[0].identity_status, "matched_outside_cohort")
        self.assertFalse(result["event_overlap"].iloc[0].storm_has_forecast)

    def test_cycle_is_not_shifted_but_other_forecast_can_cover_event(self):
        new_init = self.init + pd.Timedelta(hours=6)
        self.messages = pd.DataFrame([self.message("b#1", init=new_init)])
        self.tracks = self.points("b#1", (0, 18, 24), init=new_init)
        self.truth.loc[0, "ISO_TIME"] = new_init
        result = self.run_match()
        case = result["case_overlap"].iloc[0]
        event = result["event_overlap"].iloc[0]
        self.assertEqual(case.init_time, self.init)
        self.assertFalse(case.strict_init_present)
        self.assertFalse(case.forecast_available)
        self.assertEqual(case.unavailable_reason, "planned_init_absent")
        self.assertTrue(event.event_time_covered)
        self.assertEqual(event.available_case_count, 0)

    def test_equivalent_duplicate_files_count_once(self):
        self.messages = pd.concat([self.messages, pd.DataFrame([self.message("b#1")])],
                                  ignore_index=True)
        self.tracks = pd.concat([self.tracks, self.points("b#1")], ignore_index=True)
        result = self.run_match()
        self.assertEqual(result["storm_links"].duplicate_status.tolist(),
                         ["unique", "equivalent_duplicate"])
        self.assertEqual(result["event_overlap"].iloc[0].storm_forecast_initializations, 1)
        self.assertEqual(result["event_overlap"].iloc[0].covering_initialization_count, 1)
        self.assertEqual(result["coverage_by_lead"].iloc[0].available_cases, 1)

    def test_conflicting_intensity_duplicate_excludes_both_files(self):
        self.messages = pd.concat([self.messages, pd.DataFrame([self.message("b#1")])],
                                  ignore_index=True)
        different = self.points("b#1")
        different.loc[different.lead_hours.eq(24), "max_wind_ms"] = 31.
        self.tracks = pd.concat([self.tracks, different], ignore_index=True)
        result = self.run_match()
        self.assertTrue(result["storm_links"].duplicate_status.eq(
            "conflicting_duplicate_forecasts").all())
        self.assertFalse(result["event_overlap"].iloc[0].storm_has_forecast)
        self.assertEqual(result["case_overlap"].iloc[0].unavailable_reason,
                         "conflicting_duplicate_forecasts")

    def test_conflicting_duplicate_lead_within_message_is_rejected(self):
        different = self.tracks.loc[self.tracks.lead_hours.eq(24)].copy()
        different["latitude"] = 11.
        self.tracks = pd.concat([self.tracks, different], ignore_index=True)
        case = self.run_match()["case_overlap"].iloc[0]
        self.assertFalse(case.forecast_available)
        self.assertEqual(case.unavailable_reason, "conflicting_duplicate_forecasts")

    def test_conflicting_valid_time_is_an_input_error(self):
        self.tracks.loc[1, "valid_time"] += pd.Timedelta(hours=1)
        with self.assertRaisesRegex(ValueError, "valid_time conflicts"):
            self.run_match()

    def test_empty_forecast_preserves_every_event_and_case(self):
        self.messages = self.messages.iloc[:0]
        self.tracks = self.tracks.iloc[:0]
        result = self.run_match()
        self.assertEqual(len(result["event_overlap"]), 1)
        self.assertEqual(len(result["case_overlap"]), 1)
        self.assertFalse(result["case_overlap"].iloc[0].forecast_available)
        self.assertEqual(result["coverage_by_year"].iloc[0].available_cases, 0)


if __name__ == "__main__":
    unittest.main()
