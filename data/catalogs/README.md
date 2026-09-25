# Frozen coastal-impact cohort

The versioned 2022–2024 research cohort uses NOAA IBTrACS v04r01 as revised on
2026-09-22 and GSHHG 2.3.7. Its source and artifact hashes are recorded in
[`../manifests/coastal_catalog_2022_2024_v2026-09-22.json`](../manifests/coastal_catalog_2022_2024_v2026-09-22.json).

- `coastal_impact_events_2022_2024_v2026-09-22.csv`: 114 QC-passed final main
  A+B events from 83 storms. This is the event population for research.
- `coastal_impact_forecast_cases_2022_2024_v2026-09-22.csv`: historical
  six-hour-cycle plan with 570 initialization opportunities, five per event.
- `coastal_impact_qc_2022_2024_v2026-09-22.csv`: one QC record per event,
  including review notes and independently checked coastline distances.

Use `pd.read_csv(path, keep_default_na=False, na_values=[""])` so the North
Atlantic basin code `NA` is retained. Forecast cases and repeated coastal
episodes from one storm are not independent; cluster uncertainty by `storm_id`.

The [Weather Hub handoff](../handoffs/README.md) reselects the nearest UTC
whole-hour initialization for these 570 case IDs and records the resulting
schedule and inference controller input files.
