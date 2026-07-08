# quant-engine

Python backend for the algorithmic trading platform. Contains data ingestion, feature engineering, ML models, strategy logic, backtesting, risk management, and the FastAPI server.

## Setup

```bash
# Install all dependency groups (data feeds, ML stack, API server, dev tools)
pip install -e ".[data,ml,api,dev]"

# macOS arm64 only — required for LightGBM
brew install libomp
```

## Structure

```
quant-engine/
├── config/        Settings (pydantic-settings), structured logging
├── data/          9 feed adapters (yfinance, Alpaca, Binance, CoinGecko,
│                  NewsAPI, GDELT, Alpha Vantage, SEC EDGAR, Bloomberg)
│                  + DataStore (SQLAlchemy), DataPipeline (APScheduler)
├── features/      Technical, statistical, fundamental, sentiment, macro
│                  + order-book imbalance + FeaturePipeline
├── models/        LSTM, Transformer, GP, LightGBM, PPO RL, Ensemble + ModelRegistry
├── strategies/    Momentum, mean reversion, stat-arb, market-making, sentiment, macro factor
│                  + StrategyOrchestrator
├── backtesting/   Event-driven engine, SimulatedBroker (partial fills + sqrt slippage),
│                  portfolio, metrics, walk-forward CV
├── risk/          RiskManager, VaR/CVaR, DrawdownMonitor, correlation checker
├── execution/     PaperBroker (partial-fill mode) + AlpacaBroker + BinanceBroker + BrokerFactory
├── api/           FastAPI REST + WebSocket server
│   ├── routes/    backtest, portfolio (incl. price-history), risk, signals, strategies
│   ├── ws/        WebSocket feed (/ws/feed)
│   ├── deps.py    AppState dependency injection + require_operator OIDC seam
│   ├── schemas.py Pydantic request/response models
│   └── main.py    App entry point (lifespan, CORS, routers)
├── infra/         AWS Terraform IaC baseline (11 .tf files)
└── tests/         Full pytest suite — 641 passing, 37 model tests, 28 integration tests
    ├── api/           Unit tests for all REST routes (injected AppState)
    ├── backtesting/   Broker, engine, events, metrics, portfolio,
    │                  partial fills, slippage models
    ├── data/          Bloomberg feed
    ├── execution/     Base, factory, broker adapters, PaperBroker partial fills
    ├── features/      Pipeline, technical, statistical, order-book imbalance
    ├── integration/   Full-lifespan integration tests (TestClient, DataStore)
    ├── models/        All ML model suites (run separately via make test-models)
    ├── risk/          Correlation, limits, manager, monitor, VaR
    └── strategies/    Base, orchestrator, all 6 strategy classes
```

## Running the server

```bash
# From algo-trading/ (uses Makefile)
make api           # FastAPI at http://127.0.0.1:8000
make dev           # API + Vite dashboard in parallel

# Direct uvicorn
cd packages/quant-engine
uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```

Interactive docs: `http://127.0.0.1:8000/docs`

## Running tests

```bash
# From algo-trading/
make test               # All non-model tests (~4s, 641 passing)
make test-models        # ML model tests (two-pass for macOS OpenMP isolation)
make test-integration   # Full-lifespan integration tests (28 passing)
make test-e2e           # Playwright E2E (requires running dev server: make dev)
make test-cov           # With HTML coverage report

# Direct pytest
cd packages/quant-engine
pytest tests/ --ignore=tests/models -v --tb=short

# Specific suites
pytest tests/api/ -v
pytest tests/integration/ -v
pytest tests/backtesting/test_partial_fills.py tests/backtesting/test_slippage.py -v
pytest tests/features/test_order_book_imbalance.py -v
```

## Test counts

| Suite | Collected | Passing | Skipped | Failing |
|-------|-----------|---------|---------|---------|
| Non-model (`make test`) | 644 | 641 | 3 | 0 |
| Model (`make test-models`) | 37 | 37 | 0 | 0 |
| Integration (`make test-integration`) | 28 | 28 | 0 | 0 |
| **Total** | **709** | **706** | **3** | **0** |

The 3 skips are pre-existing platform/dependency guards in the model suite.

## API endpoints

| Method | Path | Auth required | Description |
|--------|------|--------------|-------------|
| GET | `/health` | — | Health check + uptime + trading mode |
| GET | `/` | — | API info + doc links |
| POST | `/api/backtest/run` | `require_operator` | Trigger a new backtest (async background task) |
| GET | `/api/backtest/{run_id}/status` | — | Poll run progress |
| GET | `/api/backtest/{run_id}` | — | Retrieve completed result |
| DELETE | `/api/backtest/{run_id}` | `require_operator` | Delete a cached run |
| GET | `/api/portfolio` | — | Live portfolio state (cash, positions, PnL) |
| GET | `/api/portfolio/history` | — | Equity curve history |
| GET | `/api/portfolio/trades` | — | Recent fills |
| GET | `/api/portfolio/price-history` | — | OHLCV bars from DataStore (`?ticker=&interval=&limit=`) |
| GET | `/api/risk/status` | — | Risk snapshot (VaR, drawdown, halt state) |
| POST | `/api/risk/resume` | `require_operator` | Clear a trading halt |
| GET | `/api/signals/latest` | — | Latest signals from all strategies |
| GET | `/api/strategies` | — | List all strategies |
| PATCH | `/api/strategies/{id}` | `require_operator` | Enable/disable a strategy at runtime |
| WS | `/ws/feed` | — | Real-time event stream (signals, fills, risk, heartbeat) |

**Auth note:** `require_operator` is a no-op when `OIDC_ISSUER_URL` is unset (default in dev). Set it in `.env` to enable JWT Bearer validation in production.

## Trading modes

Set `TRADING_MODE` in the root `.env` file:
- `dev`   — no live connections; PaperBroker with no API keys required
- `paper` — live data feeds + PaperBroker (simulated fills, real market data)
- `live`  — live data feeds + real order execution via Alpaca/Binance (requires API keys)

`BrokerFactory` refuses to start in `live` mode without at least one broker's keys set.

## AppState fields

`api/deps.py::AppState` is the central state container injected into every route:

| Field | Type | Description |
|-------|------|-------------|
| `broker` | `ExecutionBroker \| None` | Active broker (Paper/Alpaca/Binance) |
| `monitor` | `DrawdownMonitor \| None` | Drawdown + daily-loss monitor |
| `risk_manager` | `RiskManager \| None` | Order gate (APPROVE/SCALE_DOWN/REJECT) |
| `orchestrator` | `StrategyOrchestrator \| None` | All active strategies |
| `portfolio` | `Portfolio \| None` | Live position + PnL tracking |
| `data_store` | `DataStore \| None` | SQLAlchemy-backed market data store |
| `backtest_results` | `dict[str, dict]` | Cached backtest run results |
| `backtest_status` | `dict[str, dict]` | In-progress run tracking |
| `latest_signals` | `list[dict]` | Last signal from each strategy |
| `equity_history` | `list[float]` | Equity curve for VaR and charts |
| `trading_mode` | `str` | `"dev"` / `"paper"` / `"live"` |
| `version` | `str` | App version string |
| `started_at` | `float` | Startup timestamp for uptime |

## Execution realism features (Phase 7)

### SimulatedBroker (backtesting)

```python
from backtesting.broker import SimulatedBroker, SqrtImpactSlippage

# Square-root market impact slippage
broker = SimulatedBroker(
    slippage_model=SqrtImpactSlippage(impact_coeff=0.1),
    volume_participation_rate=0.05,   # cap fills at 5% of bar volume
    min_fill_pct=0.10,                # discard dust remainder < 10%
    fee_rate=0.001,                   # 10 bps crypto fee
)
```

### PaperBroker (paper/live execution)

```python
from execution.paper_broker import PaperBroker

# Partial fill simulation (off by default for backward compat)
broker = PaperBroker(
    partial_fill_mode=True,
    volume_participation_rate=0.05,
    simulated_bar_volume=1_000_000.0,
)
fill = broker.submit_order(order)
# fill.status == OrderStatus.PARTIAL when order > 50,000 units
# fill.metadata["remaining_qty"] holds unfilled quantity
```

### Order-book imbalance feature

```python
from features.pipeline import FeaturePipeline
from data.schemas import OrderBook

pipeline = FeaturePipeline(store=store, order_book=live_book)
# — OR update the snapshot dynamically:
pipeline.set_order_book(latest_order_book)

features = pipeline.build("AAPL", start, end)
# features["order_book_imbalance"] ∈ [-1, +1]
# NaN when no order book snapshot is set (e.g. during backtesting)
```

## mypy status

Per-module overrides active in `pyproject.toml`:

```toml
[[tool.mypy.overrides]]
module = ["api.*", "config.settings", "data.store", "execution.base", "strategies.base"]
disallow_untyped_defs = true
warn_return_any = true
```

```bash
cd packages/quant-engine
python3.11 -m mypy api/ config/settings.py data/store.py execution/base.py strategies/base.py
# Success: no issues found in 16 source files
```
