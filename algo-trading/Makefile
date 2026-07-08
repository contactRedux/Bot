# ──────────────────────────────────────────────────────────────────────────────
# Makefile — top-level developer commands for the algo-trading monorepo
#
# Usage:
#   make dev        Start the API server + dashboard in parallel
#   make api        Start only the FastAPI server (hot-reload)
#   make dashboard  Start only the Vite dev server
#   make test       Run the full Python test suite
#   make backtest   Run a backtest (pass ARGS to customise)
#   make lint       Lint Python (ruff) + TypeScript (eslint)
#   make fmt        Auto-format Python (ruff) + TypeScript (prettier)
#   make install    Install all Python + Node dependencies
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: dev api dashboard test test-models test-cov backtest lint fmt install help

# Paths
QE := packages/quant-engine
DASH := packages/dashboard

# Python interpreter — prefer python3.11 (project requirement), fall back to python3/python
PYTHON := $(shell command -v python3.11 2>/dev/null || command -v python3 2>/dev/null || echo python)
PIP    := $(PYTHON) -m pip

# ── Installation ──────────────────────────────────────────────────────────────

install: install-python install-node

install-python:
	@echo "→ Installing Python dependencies..."
	cd $(QE) && $(PIP) install -e ".[data,ml,api,dev]"

install-node:
	@echo "→ Installing Node dependencies..."
	cd $(DASH) && npm install

# ── Development servers ───────────────────────────────────────────────────────

dev:
	@echo "→ Starting API server + dashboard..."
	$(MAKE) api & $(MAKE) dashboard

api:
	@echo "→ Starting FastAPI server on http://127.0.0.1:8000 ..."
	cd $(QE) && $(PYTHON) -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000

dashboard:
	@echo "→ Starting Vite dashboard on http://localhost:5173 ..."
	cd $(DASH) && npm run dev

# ── Testing ───────────────────────────────────────────────────────────────────
#
# macOS note: LightGBM tests must run in a separate invocation from other
# ML tests (LSTM/GP/RL) due to an OpenMP library conflict on arm64 Darwin.
# 'make test-models' handles this by running two pytest invocations in order.

test:
	@echo "→ Running Python test suite (non-model tests)..."
	cd $(QE) && $(PYTHON) -m pytest tests/ --ignore=tests/models -v --tb=short

test-models:
	@echo "→ Running ML model tests (LightGBM invocation 1/2)..."
	cd $(QE) && $(PYTHON) -m pytest tests/models/test_gradient_boosting.py tests/models/test_base.py -v --tb=short
	@echo "→ Running ML model tests (PyTorch/GP invocation 2/2)..."
	cd $(QE) && $(PYTHON) -m pytest tests/models/test_models.py tests/models/test_walk_forward.py -v --tb=short

test-cov:
	@echo "→ Running tests with coverage report..."
	cd $(QE) && $(PYTHON) -m pytest tests/ --ignore=tests/models --cov=. --cov-report=html -v

# ── Backtesting ───────────────────────────────────────────────────────────────
# Example: make backtest ARGS="--strategies all --start 2022-01-01 --end 2024-01-01 --tickers AAPL MSFT BTC-USD"

ARGS ?= --strategies all --start 2023-01-01 --end 2024-01-01 --tickers AAPL MSFT

backtest:
	@echo "→ Running backtest with args: $(ARGS)"
	cd $(QE) && $(PYTHON) -m backtesting.runner $(ARGS)

# ── Linting & formatting ──────────────────────────────────────────────────────

lint: lint-python lint-node

lint-python:
	@echo "→ Linting Python with ruff..."
	cd $(QE) && $(PYTHON) -m ruff check .

lint-node:
	@echo "→ Linting TypeScript with eslint..."
	cd $(DASH) && npm run lint

fmt: fmt-python fmt-node

fmt-python:
	@echo "→ Formatting Python with ruff..."
	cd $(QE) && $(PYTHON) -m ruff format .

fmt-node:
	@echo "→ Formatting TypeScript with prettier..."
	cd $(DASH) && npm run format

# ── Help ──────────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "algo-trading monorepo — available commands:"
	@echo ""
	@echo "  make install        Install all Python + Node dependencies"
	@echo "  make dev            Start API server + dashboard (parallel)"
	@echo "  make api            Start FastAPI server only"
	@echo "  make dashboard      Start Vite dashboard only"
	@echo "  make test           Run Python test suite (data, features, strategy tests)"
	@echo "  make test-models    Run ML model tests (two-invocation: LightGBM then PyTorch)"
	@echo "  make test-cov       Run tests with HTML coverage report"
	@echo "  make backtest       Run backtest (customise with ARGS=...)"
	@echo "  make lint           Lint Python + TypeScript"
	@echo "  make fmt            Auto-format Python + TypeScript"
	@echo ""
