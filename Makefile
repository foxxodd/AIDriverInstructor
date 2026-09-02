.PHONY: install lint format typecheck test check

install:
	python -m pip install -r requirements/dev.txt

lint:
	python -m ruff check .
	python -m ruff format --check .

format:
	python -m ruff check --fix .
	python -m ruff format .

typecheck:
	python -m mypy src

test:
	python -m pytest

check: lint typecheck test
