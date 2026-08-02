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

- Commit directly to `main` and push through the configured SSH remote.
- Do not create a feature branch or use the GitHub CLI unless the user explicitly
  requests an exception.
