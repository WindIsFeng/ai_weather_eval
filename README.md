# AI Weather Evaluation

This project evaluates how well AI weather models, including Pangu-Weather,
FengWu, FuXi, GraphCast, and Aurora, predict global tropical-cyclone events
with potential coastal wind exposure during 2022–2024. A qualifying event has
IBTrACS `USA_WIND` of at least 64 kt within the coastal-contact episode or six
hours on either side; this one-minute-wind threshold defines the study's
"severe tropical cyclone" sample.

This repository contains case management, model-output adapters, verification,
statistical analysis, and plotting code. Model inference and large datasets are
managed outside this repository.

## Research Scope

- **Cases:** Coastal-contact episodes whose IBTrACS `USA_WIND` reaches at least
  64 kt within six hours of the episode. The primary sample includes coastline
  crossings and non-crossing storms whose 50 or 64 kt wind radii reach land.
- **Events:** Coastal contacts separated by more than six hours are distinct
  events. Storm identity is always the IBTrACS `SID`, never name or year, and
  statistical uncertainty is clustered by `SID`.
- **Distance:** Track-to-coast distance and crossings are computed directly
  against fixed GSHHG coastlines with WGS84 ellipsoidal geodesics. IBTrACS
  `DIST2LAND` and `LANDFALL` are not used. GSHHG land polygons classify every
  track point as land or sea and distinguish landfall from coastal exit.
- **Initializations:** Standard six-hourly forecast cycles near 24, 48, 72, 96,
  and 120 hours before the first observed landfall in an episode, or before
  closest coastal approach for episodes without landfall.
- **Metrics:** Track, ERA5-referenced gridded wind and pressure, landfall time
  and location for landfall events, and core dynamical fields.
- **Fields:** Mean sea-level pressure, 10 m winds, 500 hPa geopotential height,
  and 850/200 hPa winds. Precipitation is excluded from the initial version.

See [docs/methodology.md](docs/methodology.md) for the detailed methodology.

The frozen 2022–2024 main-track A+B cohort contains 114 quality-checked coastal
episodes from 83 storms. Its versioned event table, forecast-case plan, and
case-level QC audit are in [data/catalogs/](data/catalogs/README.md); source
revision and limitations are recorded in the
[release note](docs/coastal_catalog_release_2026-09-22.md).

## Getting Started

All project work must use the existing `ai-weather-eval` Conda environment,
which is fixed to Python 3.12. Project dependencies are not installed in bulk;
install packages with pip inside this environment only when a task requires
them. Do not use the system Python, the Conda `base` environment, uv, or another
project virtual environment.

For interactive development:

```bash
conda activate ai-weather-eval
python --version
```

For scripts and automated tasks, specify the environment explicitly:

```bash
conda run --name ai-weather-eval python <script.py>
conda run --name ai-weather-eval python -m pytest
```

Install dependencies only when needed:

```bash
conda run --name ai-weather-eval python -m pip install <package>
```

Install the local package in editable mode when the project CLI is needed:

```bash
conda run --name ai-weather-eval python -m pip install --editable .
export AI_WEATHER_EVAL_DATA=/path/to/ai_weather_eval_data
weather-eval config check --config configs/experiments/global_landfall_2022_2024.yaml
weather-eval catalog build \
  --config configs/experiments/global_landfall_2022_2024.yaml \
  --output-dir outputs/coastal_catalog_my_run
weather-eval catalog plan \
  --config configs/experiments/global_landfall_2022_2024.yaml \
  --cases-file data/catalogs/coastal_impact_events_2022_2024_v2026-09-22.csv \
  --output outputs/coastal_catalog_my_run/forecast_cases.csv
weather-eval --help
```

The `environment.yml` file records only the environment name, Python 3.12, and
pip. Python dependency declarations remain in `pyproject.toml`, but packages
are installed only as required by the current development task.

The coastal-impact catalog and forecast-case planner are implemented. Model
forecasts will be supplied separately. Forecast ingestion, verification,
statistical aggregation, and plotting remain scaffolded for
incremental implementation.

## Repository Layout

- `configs/`: Dataset, model, experiment, and figure configuration.
- `data/`: Data documentation, manifests, versioned small research catalogs, and test samples.
- `src/ai_weather_eval/`: Tested production code.
- `tests/`: Unit tests, integration tests, and compact fixtures.
- `notebooks/`: Exploration, manual quality control, and artifact review; core
  workflow logic does not belong here.
- `outputs/`: Generated run artifacts excluded from Git.
- `reports/`: Reviewed publication figures and tables that may be versioned.

## External Data

Set `AI_WEATHER_EVAL_DATA` to the root of the external data store. The
recommended layout is documented in [data/README.md](data/README.md). Do not
commit full NetCDF, GRIB, or Zarr datasets to this repository.
