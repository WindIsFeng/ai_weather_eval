# Project execution rules

## Python environment

- Use the existing Conda environment named `ai-weather-eval` for every Python,
  test, lint, notebook, and project CLI command.
- For non-interactive commands, use
  `conda run --name ai-weather-eval <command>` so execution never falls back to
  the system Python.
- For interactive development, run `conda activate ai-weather-eval` first.
- Do not create a uv environment, `.venv`, or another project-specific Conda
  environment.
- Install a dependency only when the current task requires it, using
  `conda run --name ai-weather-eval python -m pip install <package>`.
- Do not bulk-install optional dependency groups unless the user requests it.

## Git workflow

- Commit directly to `main` unless the user explicitly requests a branch.
- Use the configured Git remote and any suitable authenticated transport; the
  GitHub CLI may be used when available and authenticated.

## Figure defaults

- Export figures at 300 DPI in TIFF (`.tif`) format unless the user explicitly
  requests another resolution or format.
- Use Arial at 16 pt for all figure text. If Arial is unavailable on the host,
  use Liberation Sans as the metric-compatible fallback.
- Do not add figure titles unless the user explicitly requests one.
