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

.PHONY: dev api dashboard test backtest lint fmt install help

# Paths
QE := packages/quant-engine
DASH := packages/dashboard

# Python interpreter (uses the venv if present)
PYTHON := $(shell command -v python3 2>/dev/null || echo python)
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
	@echo "→ Starting FastAPI server on http://localhost:8000 ..."
	cd $(QE) && $(PYTHON) -m uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

dashboard:
	@echo "→ Starting Vite dashboard on http://localhost:5173 ..."
	cd $(DASH) && npm run dev

# ── Testing ───────────────────────────────────────────────────────────────────

test:
	@echo "→ Running Python test suite..."
	cd $(QE) && $(PYTHON) -m pytest tests/ -v --tb=short

test-cov:
	@echo "→ Running tests with coverage report..."
	cd $(QE) && $(PYTHON) -m pytest tests/ --cov=. --cov-report=html -v

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
	@echo "  make install     Install all Python + Node dependencies"
	@echo "  make dev         Start API server + dashboard (parallel)"
	@echo "  make api         Start FastAPI server only"
	@echo "  make dashboard   Start Vite dashboard only"
	@echo "  make test        Run Python test suite"
	@echo "  make test-cov    Run tests with HTML coverage report"
	@echo "  make backtest    Run backtest (customise with ARGS=...)"
	@echo "  make lint        Lint Python + TypeScript"
	@echo "  make fmt         Auto-format Python + TypeScript"
	@echo ""
