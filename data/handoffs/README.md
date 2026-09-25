# Weather Hub inference handoff

The files in this directory turn the frozen 114-event, 570-case coastal-impact
cohort into an input batch for the adjacent `weather_hub` project. The same
seven-column case file works for Pangu-Weather, FengWu, FuXi, GraphCast, and
Aurora through Weather Hub.

- [`weather_hub_cases_2022_2024_v2026-09-22.csv`](weather_hub_cases_2022_2024_v2026-09-22.csv)
  is the file to pass to `weather-hub run --cases`. Its `case_id` is the frozen
  catalog's `forecast_case_id`, so every model result can be joined to one
  planned forecast case.
- [`weather_hub_case_index_2022_2024_v2026-09-22.csv`](weather_hub_case_index_2022_2024_v2026-09-22.csv)
  links each forecast to the observed event, nominal and actual lead times,
  location, and final forecast valid time. It preserves subsecond reference
  times where present.
- [`weather_hub_manifest_2022_2024_v2026-09-22.json`](weather_hub_manifest_2022_2024_v2026-09-22.json)
  records source and output hashes, counts, and the Weather Hub contract
  revision checked for this handoff.

`init_time` is the selected 00/06/12/18 UTC cycle from the frozen forecast
plan. `forecast_hours` is the first multiple of six hours at or after the
observed `reference_time`; output is every six hours. This gives the forecast
an output at or after the event without changing its planned initialization.
The resulting lengths are 24/30, 48/54, 72/78, 96/102, and 120/126 hours.
All 570 cases are matched; there are 510 distinct initialization times.

For example, after setting up Weather Hub and the selected model environment:

```bash
weather-hub run --model pangu \
  --cases /scratch/hufeng/ai_weather_eval/data/handoffs/weather_hub_cases_2022_2024_v2026-09-22.csv \
  --dry-run
```

Repeat with `fengwu`, `fuxi`, `graphcast`, or `aurora` for other models. Remove
`--dry-run` to launch inference. Weather Hub writes the model result index by
`case_id`; join it with the case index here for verification. Model initial
fields are required at `T−6h` and `T`, where `T` is `init_time`.

Regenerate the handoff from the frozen catalogs with:

```bash
conda run --name ai-weather-eval python scripts/build_weather_hub_handoff.py
```
