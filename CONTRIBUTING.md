# Contributing

Use Python 3.10 and create a virtual environment before installing the project:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Before committing, run `python -m ruff check .`, `python -m ruff format --check .`,
`python -m mypy src`, and `python -m pytest`. Test coverage must remain at or above 75%.

Use short-lived branches and Conventional Commit messages such as `feat: add UDP capture`.
Never commit captures from `drive_logs/` or generated files from `compiled_trips/`.
