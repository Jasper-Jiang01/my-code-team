.PHONY: install dev lint format test run

install:
	pip install -e ".[dev]"

dev:
	pip install -e ".[dev]"

lint:
	ruff check src/
	mypy src/

format:
	ruff format src/

test:
	pytest tests/ -v

eval:
	pytest tests/test_eval_harness.py -v

run:
	langgraph dev
