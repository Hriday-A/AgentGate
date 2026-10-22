# Commands use `python -m agentgate` from the repo root rather than the
# `agentgate` script: on some macOS setups files in .venv get the hidden flag,
# and Python 3.13 skips hidden .pth files, which breaks editable installs.
PY := .venv/bin/python

.PHONY: setup portals test run run-local

setup:
	python3 -m venv .venv
	$(PY) -m pip install -e '.[dev]'
	$(PY) -m playwright install chromium

portals:
	$(PY) -m agentgate portals

test:
	$(PY) -m pytest -q

run:
	$(PY) -m agentgate run

run-local:
	$(PY) -m agentgate run --model ollama:llama3.2
