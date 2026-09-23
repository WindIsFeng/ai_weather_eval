# Frozen 2022–2024 coastal-impact cohort

**Release ID:** `coastal-impact-2022-2024-ibtracs-v04r01-2026-09-22-gshhg-2.3.7`

This is the fixed research population for evaluating 2022–2024 global tropical
cyclone forecasts. The unit is a coastal-contact episode, not a storm or a
forecast cycle. A selected episode uses an IBTrACS `main` track, reaches
`USA_WIND >= 64 kt` during the contact interval or within six hours on either
side, and belongs to tier A or B under [the project method](methodology.md).
Wind-radius contact represents potential coastal exposure, not observed damage.

## Frozen inputs

- [NOAA IBTrACS v04r01 `since1980` CSV](https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/csv/ibtracs.since1980.list.v04r01.csv),
  `Last-Modified: 2026-09-22 08:57:07 UTC`, ETag
  `"895c23c-65c0e8d203a37"`. The local 18,034,959-byte file reconstructs
  the contiguous source range 126,000,000–144,032,315 with the source header
  and units row, then drops the partial first record. SHA-256:
  `290b57e7d353b5a7db6bab9ea818144f4cb835c58e17a7d4a5bd7ca0fe65d028`.
  Its 38,205 rows span September 2020 through September 2026, covering all
  study years. This is a frozen **study-range extract**, not a copy of the
  entire 144,032,316-byte NOAA file.
- [GSHHG 2.3.7 shapefile archive](https://ftp.soest.hawaii.edu/gshhg/gshhg-shp-2.3.7.zip),
  using intermediate-resolution level-1 coastline polygons, SHA-256
  `8dbbe7e071e77e9e75f2d639239099ebca8d5c16d6a07df8169729d49f15cf41`.

The exact local paths, configuration hashes, QC script hash, table hashes, and
row counts are in [the release manifest](../data/manifests/coastal_catalog_2022_2024_v2026-09-22.json).
Large frozen source files are under ignored `data/raw/`; the small approved
tables are tracked in [data/catalogs](../data/catalogs/README.md).

## Selection and QC

The frozen inputs rebuilt 155 coastal-contact events. Three use provisional
tracks, leaving 152 `main` events. Tier A+B contains **114 episodes from 83
storms**: 83 tier A and 31 tier B; 85 have a landfall anchor and 29 use closest
coastal approach. There are 570 matched six-hourly forecast opportunities at
the five nominal lead times.

Every selected event passed the reproducible checks in
[`scripts/qc_coastal_catalog.py`](../scripts/qc_coastal_catalog.py). The audit
verified source hashes; storm identity and `main` track; basin and name;
period, coordinate and 64 kt eligibility; tier and contact flags; episode wind
maximum; coastal distance and sea-to-land anchor geometry; unique keys; and all
forecast cycle times and links. The maximum distance between a recorded
landfall anchor and the fixed shoreline was 0.293 km. Two anchors, Gombe and
Otis, required the documented 500 m along-track direction tolerance rather
than 50 m because their computed anchors are about 0.2–0.3 km from the
shoreline. Neither changes cohort membership.

**QC outcome: 114 passed, 0 failed.** Thirty-eight cases have overlapping
review notes in the [event-level audit](../data/catalogs/coastal_impact_qc_2022_2024_v2026-09-22.csv):

| Review note | Events | Resolution |
| --- | ---: | --- |
| Five or more landfall crossings | 15 | Recomputed crossings match each event's recorded count; no consecutive landfall pair was within both 1 km and 1 h. Multiple coast/island contacts are retained within one episode. |
| 34 kt radius coverage below 80% | 11 | Tier A/B support and other eligibility checks passed. Keep a coverage sensitivity analysis for downstream comparisons. |
| `USA_WIND` coverage below 80% | 4 | Each event's observed peak within the allowed contact window was verified; keep a coverage sensitivity analysis. |
| Anchor outside the contact-point span | 16 | All anchors lie within the allowed ±6 h padding and pass shoreline geometry checks. |
| Non-landfall contact farther than 100 km | 3 | Earl, Fiona, and Guchol each have 50 kt wind-radius contact in the source-derived track points. |

These note groups overlap; they are not exclusions. The approved event table
sets `qc_status=passed` for every row, and the separate audit retains the
case-specific evidence. Use `storm_id` as the clustered unit for uncertainty:
114 episodes and 570 forecast opportunities do not represent 114 or 570
independent storms.

## Reproduce locally

Set the data root to this project's ignored `data/` directory, or stage the
same checksum-verified sources in another root. The fixed inputs are required;
a fresh clone contains the approved small tables but not the raw files.
Choose a new, unused output directory for each rebuild.

```bash
export AI_WEATHER_EVAL_DATA="$(pwd)/data"
conda run --name ai-weather-eval python -m ai_weather_eval.cli catalog build \
  --config configs/experiments/global_landfall_2022_2024.yaml \
  --output-dir outputs/coastal_catalog_rebuild_20260923
conda run --name ai-weather-eval python -m ai_weather_eval.cli catalog plan \
  --config configs/experiments/global_landfall_2022_2024.yaml \
  --cases-file outputs/coastal_catalog_rebuild_20260923/final_main_primary_coastal_impact_cases.csv \
  --output outputs/coastal_catalog_rebuild_20260923/forecast_cases.csv
conda run --name ai-weather-eval python scripts/qc_coastal_catalog.py \
  --catalog-dir outputs/coastal_catalog_rebuild_20260923
```

The approved cohort for analysis is
[`data/catalogs/coastal_impact_events_2022_2024_v2026-09-22.csv`](../data/catalogs/coastal_impact_events_2022_2024_v2026-09-22.csv).
