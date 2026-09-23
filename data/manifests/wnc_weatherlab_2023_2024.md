# WN-C Weather Lab historical tracks, 2023–2024

Accessed 2026-09-23 UTC. Data are stored locally under `data/raw/` and excluded
from Git. The integrity and download status of every Weather Lab file are in
`outputs/wnc_2023_2024/download_manifest.csv` (SHA-256
`272d4044c14ff1933e34cb415d3cc6070804cd9f5384fab1cfeaab98184b9147`).

| Source | Local location | Inventory |
| --- | --- | --- |
| [Google DeepMind Weather Lab cyclone downloads](https://developers.google.com/weathernext/guides/weatherlab) | `data/raw/forecasts/wnc_weatherlab/FNV3P2/{ensemble,ensemble_mean}/{2023,2024}/` | 2,924 six-hourly cycles; 5,848/5,848 CSV files downloaded; 938,849,979 bytes total; 50-member and ensemble-mean products |
| [NOAA IBTrACS v04r01 since1980 CSV](https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/csv/ibtracs.since1980.list.v04r01.csv) | `data/raw/ibtracs/ibtracs_2023_2024_range.csv` | Contiguous byte range 129,000,000–141,999,999 of the 144,032,316-byte source, plus its original header/units lines; covers 2021-08 to 2025-10 and hence all 2023–2024 records |

The NOAA source had `Last-Modified: Tue, 22 Sep 2026 08:57:07 GMT` and ETag
`"895c23c-65c0e8d203a37"`. The reconstructed extract has 27,471 data rows,
13,002,477 bytes and SHA-256
`70a7aa2fbce276202c755aa0e70091f4964ea0ab8cdfcc158c4f408e3fde2d17`.
The source metadata and byte interval are recorded in
`data/raw/ibtracs/ibtracs_2023_2024_range.json`. Range requests were guarded
with the source ETag and checked for exact length. This is an extract of the
2026-09-22 revision. At the time of this WN-C run, the coastal-catalog
configuration still pinned a 2026-07-28 file; it now pins a separate frozen
study-range extract of the 2026-09-22 revision.

Weather Lab files follow this URL pattern:

```
https://deepmind.google.com/science/weatherlab/download/cyclones/FNV3P2/{product}/paired/csv/FNV3P2_{YYYY}_{MM}_{DD}T{HH}_00_paired.csv
```

`product` is `ensemble` or `ensemble_mean`. The files are historical and are
marked CC BY 4.0 in their headers. The 2023 CSV sometimes includes an optional
`lead_time_hours` column absent from 2024 files. `track_id` also differs from
IBTrACS `USA_ATCF_ID` for some storms, notably Southern Indian Ocean storms,
Southern Hemisphere seasonal years, and pre-designation invest IDs. Preserve
original files and use the audited position-based link in the evaluation script.

Reproduce downloads with the existing `ai-weather-eval` Conda environment:

```
conda run --name ai-weather-eval python scripts/download_wnc_weatherlab.py
conda run --name ai-weather-eval python scripts/download_ibtracs_range.py
```

The Google file product name `FNV3P2` is retained verbatim. Its per-year
training-checkpoint mapping was not established from file metadata; do not
infer it from the filename alone.
