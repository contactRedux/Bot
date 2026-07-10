# quant-engine

Python backend for the algorithmic trading platform. Contains data ingestion, feature engineering, ML models, strategy logic, Bayesian optimisation, walk-forward validation, backtesting, risk management, live trading engine, and the FastAPI server.

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
├── config/        Settings (pydantic-settings), strategy_config.yaml, structlog
├── data/          9 feed adapters (yfinance, Alpaca, Binance, CoinGecko,
│                  NewsAPI, GDELT, Alpha Vantage, SEC EDGAR, Bloomberg)
│                  + DataStore (SQLAlchemy, dialect-agnostic), DataPipeline (APScheduler)
├── features/      Technical, statistical, fundamental, sentiment, macro,
│                  order-book imbalance + FeaturePipeline
├── models/        LSTM, Transformer, GP, LightGBM, PPO RL, Ensemble + ModelRegistry
├── strategies/    9 strategy classes + StrategyOrchestrator
│   ├── momentum.py           LSTM+Transformer ensemble, ADX filter, cooldown
│   ├── mean_reversion.py     Bollinger Band z-score, ATR stop-loss
│   ├── stat_arb.py           Engle-Granger cointegration, OU half-life filter
│   ├── market_making.py      Avellaneda-Stoikov RL-adjusted quotes, inventory skew
│   ├── sentiment.py          FinBERT news sentiment z-score, decay weighting
│   ├── macro_factor.py       VIX regime × yield curve × earnings surprise
│   ├── kalman_trend.py       1D Kalman filter, trades normalised innovation ν/√S
│   ├── kelly_vol.py          Fractional Kelly + vol targeting (Moreira & Muir 2017)
│   └── vwap_reversion.py     VWAP deviation %, ATR volatility + volume filters
├── backtesting/   Event-driven engine, SimulatedBroker (partial fills + sqrt slippage),
│   │              portfolio, metrics, walk-forward CV, Bayesian optimiser
│   ├── engine.py             Look-ahead safe simulation loop
│   ├── optimizer.py          Bayesian HPO — Optuna TPE sampler
│   ├── walkforward.py        Expanding-window OOS validation
│   ├── broker.py             SimulatedBroker, SqrtImpactSlippage, partial fills
│   ├── portfolio.py          Cash + position + PnL accounting
│   ├── metrics.py            Sharpe, Sortino, Calmar, CAGR, max drawdown, profit factor
│   └── report.py             BacktestReport serialisation
├── risk/          RiskManager, VaR/CVaR, DrawdownMonitor, correlation checker
├── execution/     TradingEngine (live loop), PaperBroker (partial-fill mode),
│                  AlpacaBroker, BinanceBroker, BrokerFactory
├── api/           FastAPI REST + WebSocket server
│   ├── routes/    ai_analyst, analysis, backtest, news, optimize, portfolio,
│   │              risk, signals, strategies, trading
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
    └── strategies/    Base, orchestrator, all 9 strategy classes
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
| GET | `/api/backtest/list` | — | List all cached runs |
| DELETE | `/api/backtest/{run_id}` | `require_operator` | Delete a cached run |
| POST | `/api/backtest/walkforward` | `require_operator` | Trigger walk-forward OOS validation |
| POST | `/api/optimize/run` | `require_operator` | Trigger Bayesian HPO (Optuna TPE) |
| GET | `/api/optimize/{run_id}/status` | — | Poll optimisation progress |
| GET | `/api/optimize/{run_id}` | — | Retrieve optimisation result |
| GET | `/api/optimize/spaces` | — | List available parameter search spaces |
| GET | `/api/news` | — | Recent news articles with sentiment scores |
| GET | `/api/portfolio` | — | Live portfolio state (cash, positions, PnL) |
| GET | `/api/portfolio/history` | — | Equity curve history |
| GET | `/api/portfolio/trades` | — | Recent fills |
| GET | `/api/portfolio/price-history` | — | OHLCV bars (`?ticker=&interval=&limit=`) |
| GET | `/api/risk/status` | — | Risk snapshot (VaR, drawdown, halt state) |
| POST | `/api/risk/resume` | `require_operator` | Clear a trading halt |
| GET | `/api/signals/latest` | — | Latest signals from all strategies |
| GET | `/api/strategies` | — | List all strategies |
| PATCH | `/api/strategies/{id}` | `require_operator` | Enable/disable a strategy at runtime |
| GET | `/api/trading/status` | — | TradingEngine status (running, tickers, loops) |
| POST | `/api/trading/start` | `require_operator` | Start the live trading loop |
| POST | `/api/trading/stop` | `require_operator` | Stop the live trading loop |
| POST | `/api/trading/tickers` | `require_operator` | Update the ticker universe |
| WS | `/ws/feed` | — | Real-time event stream (signals, fills, portfolio, news, risk, heartbeat) |

**Auth note:** `require_operator` is a no-op when `OIDC_ISSUER_URL` is unset (default in dev). Set it in `.env` to enable JWT Bearer validation in production.

## Trading modes

Set `TRADING_MODE` in the root `.env` file:
- `dev`   — no live connections; PaperBroker with no API keys required; engine does **not** auto-start
- `paper` — live data feeds + PaperBroker (simulated fills, real market data); engine **auto-starts**
- `live`  — live data feeds + real order execution via Alpaca/Binance (requires API keys); engine **auto-starts**

`BrokerFactory` refuses to start in `live` mode without at least one broker's keys set.

Use `POST /api/trading/start|stop` or the `/strategies` dashboard page to control the engine at runtime.

## Strategies

All strategies inherit from `BaseStrategy` and are registered via `strategy_config.yaml`:

| ID | Class | Math basis | Key config |
|----|-------|-----------|-----------|
| `momentum` | `MomentumStrategy` | LSTM+Transformer ensemble, Harvey (1993) momentum | `entry_threshold`, `cooldown_bars`, `stop_loss_pct` |
| `mean_reversion` | `MeanReversionStrategy` | Bollinger Bands, z-score, ATR | `lookback_bars`, `entry_z_score`, `stop_atr_multiplier` |
| `stat_arb` | `StatArbStrategy` | Engle-Granger cointegration, OU process | `entry_z_score`, `max_half_life_bars`, `default_pairs` |
| `market_making` | `MarketMakingStrategy` | Avellaneda-Stoikov, PPO RL quote adjustment | `base_half_spread`, `max_inventory`, `inventory_skew_factor` |
| `sentiment` | `SentimentStrategy` | FinBERT, exponential decay weighting | `sentiment_window_hours`, `entry_z_score`, `min_article_count` |
| `macro_factor` | `MacroFactorStrategy` | VIX regime, yield curve, PEAD | `vix_fear_threshold`, `fear_reduction_factor` |
| `kalman_trend` | `KalmanTrendStrategy` | Harvey (1989) 1D Kalman filter | `observation_noise`, `process_noise`, `entry_threshold` |
| `kelly_vol` | `KellyVolStrategy` | Thorp (1967) Kelly, Moreira & Muir (2017) vol targeting | `vol_target_pct`, `kelly_fraction`, `lookback_bars` |
| `vwap_reversion` | `VWAPReversionStrategy` | VWAP microstructure, ATR filter | `vwap_window`, `entry_band_pct`, `atr_filter` |

## Bayesian optimisation

```python
from backtesting.optimizer import StrategyOptimizer
from strategies.kalman_trend import KalmanTrendStrategy
from strategies.orchestrator import StrategyOrchestrator

optimizer = StrategyOptimizer(
    bars=bars_dict,
    strategy_factory=lambda trial: KalmanTrendStrategy(
        config={**StrategyOptimizer.kalman_trend_space(trial), "enabled": True},
        tickers=["AAPL", "NVDA"],
    ),
    orchestrator_factory=lambda s: StrategyOrchestrator(strategies=s, config={}),
    n_trials=50,
    objective="sharpe",   # sharpe | sortino | calmar | total_return
)
result = optimizer.run()
print(result.best_params)  # {'observation_noise': 0.82, 'process_noise': 0.03, ...}
```

Available search spaces: `momentum_space`, `mean_reversion_space`, `kelly_vol_space`, `kalman_trend_space`, `vwap_reversion_space`.

## Walk-forward validation

```python
from backtesting.walkforward import WalkForwardBacktest

wfb = WalkForwardBacktest(
    bars=bars_dict,
    orchestrator_factory=make_orchestrator,  # fresh instance per fold
    n_splits=4,
    oos_size_days=252,
    min_train_days=365,
    initial_capital=100_000.0,
)
results = wfb.run()
agg = results.aggregate_metrics()
# agg['sharpe_ratio'] → {'mean': 0.92, 'std': 0.34, 'min': 0.45, 'max': 1.37}
```

## AppState fields

`api/deps.py::AppState` is the central state container injected into every route:

| Field | Type | Description |
|-------|------|-------------|
| `broker` | `ExecutionBroker \| None` | Active broker (Paper/Alpaca/Binance) |
| `monitor` | `DrawdownMonitor \| None` | Drawdown + daily-loss monitor |
| `risk_manager` | `RiskManager \| None` | Order gate (APPROVE/SCALE_DOWN/REJECT) |
| `orchestrator` | `StrategyOrchestrator \| None` | All active strategies |
| `trading_engine` | `TradingEngine \| None` | Live trading loop controller |
| `portfolio` | `Portfolio \| None` | Live position + PnL tracking |
| `data_store` | `DataStore \| None` | SQLAlchemy-backed market data store |
| `backtest_results` | `dict[str, dict]` | Cached backtest / optimisation results |
| `backtest_status` | `dict[str, dict]` | In-progress run tracking |
| `latest_signals` | `list[dict]` | Last 500 signals from all strategies |
| `equity_history` | `list[float]` | Equity curve (last 2000 points) for VaR and charts |
| `trading_mode` | `str` | `"dev"` / `"paper"` / `"live"` |
| `version` | `str` | App version string |
| `started_at` | `float` | Startup timestamp for uptime |

## Execution realism features

### SimulatedBroker (backtesting)

```python
from backtesting.broker import SimulatedBroker, SqrtImpactSlippage

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

pipeline = FeaturePipeline(store=store, order_book=live_book)
pipeline.set_order_book(latest_order_book)   # update snapshot dynamically
features = pipeline.build("AAPL", start, end)
# features["order_book_imbalance"] ∈ [-1, +1]
# NaN when no order book snapshot is available (e.g. during backtesting)
```

## AI Analyst

The AI Analyst (`api/routes/ai_analyst.py`) is a standalone endpoint that assembles live data from `AppState` and calls an LLM to produce a plain-English analyst report.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/ai/analyse` | Generate a report for the given tickers and focus |
| `GET` | `/api/ai/history` | Last N cached reports (in-memory) |

### Request body

```json
{
  "tickers":        ["AAPL", "MSFT"],  // empty = use active positions
  "include_trades": true,
  "include_news":   true,
  "focus":          "full"            // full | risk | trades | market | outlook
}
```

### Report fields

```json
{
  "summary":            "...",
  "market_commentary":  "...",
  "trade_rationale":    "...",
  "risk_assessment":    "...",
  "outlook":            "...",
  "key_points":         ["...", "..."],
  "provider":           "openai",
  "model":              "gpt-4o",
  "context_snapshot":   {}   // full data payload sent to the LLM
}
```

### Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `LLM_PROVIDER` | `openai` | `openai` or `anthropic` |
| `LLM_API_KEY` | — | API key; blank enables offline rule-based fallback |
| `LLM_MODEL` | `gpt-4o` / `claude-3-5-sonnet-20241022` | Model override |
| `LLM_MAX_TOKENS` | `1200` | Max response tokens |

**Offline mode:** when `LLM_API_KEY` is unset, `_offline_report()` generates all five sections deterministically from the same live data — no LLM call, no cost, identical response structure.

**Optional deps:** install with `pip install -e ".[ai]"` to get `openai>=1.35` and `anthropic>=0.28`.

---

## Known fixes applied

| File | Issue | Fix |
|------|-------|-----|
| `data/feeds/yfinance_feed.py` | `multi_level_column` kwarg removed in yfinance ≥ 0.2.31 | Introspect signature at runtime; flatten MultiIndex columns |
| `data/feeds/alpaca_feed.py` | `stream.run()` conflicts with running asyncio event loop | Run stream in `ThreadPoolExecutor`; use `run_coroutine_threadsafe` for queue |
| `backtesting/metrics.py` | CAGR computed from unsorted timestamps; `datetime.now()` seed skewed duration | Sort timestamps before computing `n_calendar_days` |
| `api/main.py` | `StatArbStrategy` called with `tickers=` but constructor requires `pairs=` | Separate StatArb from generic strategy loop; construct with `pairs=` |
| `backtesting/runner.py` | `get_settings` function doesn't exist; strategy constructors called with wrong arg order | Import `settings` singleton; use `config=` / `tickers=` keyword args |

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
