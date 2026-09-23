# Exploratory catalog check — 2026-09-23

This check exercises the event selector and forecast-case planner against recent
official best tracks. It is **not** the publication catalog: the experiment
configuration pins an IBTrACS file downloaded on 2026-07-28, while this check
uses the current official file modified on 2026-09-22.

## Inputs and method

- IBTrACS v04r01 `since1980` CSV from NOAA NCEI, `Last-Modified` 2026-09-22
  08:57:07 UTC, `ETag` `"895c23c-65c0e8d203a37"`. Only byte range
  `126000000–144032315` was downloaded for this check. After discarding its
  partial first record, this range begins in 2020 and includes the complete
  2022–2024 study period. The official CSV header was taken from the same
  revision. The reconstructed exploratory CSV has SHA-256
  `290b57e7d353b5a7db6bab9ea818144f4cb835c58e17a7d4a5bd7ca0fe65d028`.
- GSHHG 2.3.7 shapefile archive, SHA-256
  `8dbbe7e071e77e9e75f2d639239099ebca8d5c16d6a07df8169729d49f15cf41`;
  intermediate-resolution level-1 shoreline.
- IBTrACS `TRACK_TYPE=main`; reference time from first landfall in an episode
  or closest coastal approach without landfall; 2022–2024 reference-time period.
  Primary sample uses tiers A+B and retains the ±6 h, 64 kt rule.

## Results

| Check | Result |
| --- | ---: |
| All coastal-contact events | 152 |
| Primary A+B events | 114 |
| Storms represented in A+B | 83 |
| A+B landfall events | 85 |
| A+B non-landfall coastal-approach events | 29 |
| A+B planned forecast cases at five nominal leads | 570 |
| Planned cases without a cycle within 3 h | 0 |
| Duplicate case or forecast-case IDs | 0 |
| Reference-event/landfall flag disagreements | 0 |

All cases have reference years in 2022–2024 (45, 59, and 48 events by year).
The exploratory run uses no model forecasts or ERA5 fields and therefore does
not estimate model skill. Rebuild and audit the formal catalog using the full
IBTrACS file with the SHA-256 pinned in `configs/datasets/ibtracs.yaml` before
publication.

## Saved results

The exploratory tables are preserved under `outputs/coastal_catalog_2022_2024/`:

- `final_main_primary_coastal_impact_cases.csv`: the 114 A+B events, including
  event IDs, reference times, latitude, and longitude.
- `coastal_impact_cases.csv`: all 152 coastal-contact events.
- `forecast_cases.csv`: the 570 planned forecast cases for the A+B events.
- `manifest.json`: counts, source provenance, and file checksums.

The exact exploratory IBTrACS input is preserved at
`data/raw/ibtracs/ibtracs_recent_exploratory.csv`. These data and generated
tables are ignored by Git, so a fresh clone must regenerate or receive them
separately. They are exploratory results, not the publication catalog.

When loading these CSVs with pandas, use
`pd.read_csv(path, keep_default_na=False, na_values=[""])`. The North Atlantic
basin code is `NA`, which pandas otherwise interprets as a missing value.

## Publication-readiness check

Re-running the current selector on the preserved exploratory IBTrACS input and
the GSHHG 2.3.7 shoreline reproduced the same 152 coastal-event IDs and 114
A+B event IDs. The 114 selected rows have no missing event IDs, storm IDs,
basin codes, reference times, coordinates, or tiers; event IDs are unique, and
all 570 forecast-case IDs are unique and link to the 114 selected events.

The sample is still a candidate for the formal catalog: all 114 rows have
`qc_status=pending`, and the preserved September 2026 IBTrACS extract differs
from the full July 2026 file pinned in `configs/datasets/ibtracs.yaml`.
Before treating the cohort as final, choose and freeze one IBTrACS revision,
align the dataset configuration and manifest with that exact source, rerun the
catalog, and complete case-level quality control of coastal contacts and
landfall anchors. Record any exclusions or corrections in a versioned audit.
