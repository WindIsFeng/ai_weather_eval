# Frozen source extract

`ibtracs.since1980.2026-09-22.study_range.csv.gz` is a deterministic gzip
copy of the NOAA IBTrACS v04r01 study-range extract used for the 2022–2024
coastal cohort. Its compressed SHA-256 is
`9d04b47b18253bcac4068f5152bc7c4a1006b38ba0bfc0e66838e7502616e869`;
decompression yields SHA-256
`290b57e7d353b5a7db6bab9ea818144f4cb835c58e17a7d4a5bd7ca0fe65d028`.
The [release manifest](../manifests/coastal_catalog_2022_2024_v2026-09-22.json)
records the NOAA source revision, ETag, and byte range.

The GSHHG 2.3.7 archive remains in ignored `data/raw/` because it is about
149 MB. Download the exact archive listed in the release manifest, verify its
SHA-256, and run `conda run --name ai-weather-eval python
scripts/restore_frozen_coastal_sources.py --data-root data` from the repository
root. The script restores this compressed IBTrACS extract and the four GSHHG
intermediate-resolution level-1 shapefile components.
