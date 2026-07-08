# Algorithmic Trading Platform — Project Plan

## Top-Level Overview

Build a full-stack algorithmic trading platform as a **single monorepo** containing two top-level packages:

1. **`packages/quant-engine/`** — Python package containing all ML models, quantitative strategies, data ingestion, backtesting, risk management, and a REST/WebSocket API surface.
2. **`packages/dashboard/`** — TypeScript/React frontend for visualizing signals, portfolio PnL, strategy performance, and live news sentiment.

The project is designed to be built incrementally: start with a paper-trading simulation and backtesting engine, then graduate to live execution via a brokerage API. It covers US equities and crypto across multiple strategy families: market-making, statistical arbitrage, momentum/trend-following, mean reversion, news sentiment, and macro factor models. The codebase is heavily commented and scaffolded for someone with strong theoretical backgrounds who is building practical engineering skills.

Options chain trading (Black-Scholes, Greeks) is explicitly **Phase 2** and not in scope here.

---

## Monorepo Layout

```
algo-trading/                          # Git root
├── packages/
│   ├── quant-engine/                  # Python backend
│   │   ├── data/                      # Data ingestion, normalization, storage
│   │   ├── features/                  # Feature engineering (technical, macro, NLP)
│   │   ├── models/                    # ML/DL model definitions and training
│   │   ├── strategies/                # Strategy logic (all strategy families)
│   │   ├── backtesting/               # Event-driven simulation engine
│   │   ├── risk/                      # Position sizing, drawdown controls, VaR
│   │   ├── execution/                 # Order routing (paper + live brokerage adapters)
│   │   ├── api/                       # FastAPI REST + WebSocket endpoints
│   │   ├── db/                        # Database schemas and migrations
│   │   ├── config/                    # Strategy configs, hyperparameters
│   │   ├── tests/                     # All Python tests
│   │   ├── docs/                      # Concept explainers and learning guides
│   │   └── pyproject.toml
│   └── dashboard/                     # TypeScript/React frontend
│       ├── src/
│       │   ├── components/            # Chart panels, signal tables, news feed
│       │   ├── pages/                 # Dashboard, Backtest Explorer, Live Monitor
│       │   ├── hooks/                 # WebSocket feeds, API queries
│       │   └── store/                 # Zustand state slices
│       ├── package.json
│       └── vite.config.ts
├── .env.example                       # All env vars documented in one place
├── Makefile                           # Top-level commands: make dev, make backtest, make test
└── README.md
```

---

## Data Sources

All sources below are included — the system ingests from all of them simultaneously. Feeds are weighted by reliability and latency in the feature pipeline.

| Source | Coverage | Cost | Role in system |
|---|---|---|---|
| **Yahoo Finance** (`yfinance`) | Equities daily/intraday | Free | Primary historical OHLCV for backtesting |
| **Alpaca Market Data** | Equities, crypto real-time | Free tier + paid | Primary real-time equities stream; also handles order execution |
| **Polygon.io** | Equities, crypto, tick data | $29–$200/mo | Upgrade path for higher-resolution data; needed for Phase 2 options |
| **CoinGecko** | Crypto OHLCV | Free | Historical crypto for backtesting |
| **Binance API** | Crypto real-time + order book | Free | Primary real-time crypto stream |
| **NewsAPI** | News headlines | Free tier (100 req/day) | Rapid news ingestion; primary for MVP |
| **GDELT** | Global news event database | Free | Broad macro news signal; supplements NewsAPI |
| **Alpha Vantage** | Equities, FX, fundamentals | Free tier (5 req/min) | Fundamental data (P/E, EPS, revenue) for macro factor model |
| **SEC EDGAR** | 10-K/10-Q/earnings filings | Free | Earnings surprise signals and fundamental features |

**Starting recommendation:** Use Alpaca + yfinance + CoinGecko + NewsAPI for the initial build. Add GDELT, Alpha Vantage, and SEC EDGAR in Sub-Task 2 as additional feed implementations. Upgrade to Polygon.io when live trading demands tick-level precision.

---

## Sub-Tasks

---

### Sub-Task 1 — Monorepo Scaffolding and Environment Setup

**Status:** `[x] complete`

**What was built**
- `algo-trading/` monorepo with full directory tree, `.gitignore`, `.env.example`
- `packages/quant-engine/pyproject.toml` with dependency groups: `data`, `ml`, `api`, `dev`
- `config/settings.py` — `pydantic-settings` with `TradingMode` enum (`dev`/`paper`/`live`)
- `config/strategy_config.yaml` — full per-strategy YAML config for all 6 strategies
- `config/logging.py` — structured `structlog` logger
- `packages/dashboard/` — Vite + React + TypeScript scaffold with Tailwind CSS dark mode
- Root `Makefile` with `dev`, `test`, `test-models`, `backtest`, `lint`, `fmt`, `install` targets
- All core Python deps installed in `.venv` at `packages/quant-engine/.venv`

**Known issues / deviations**
- `pandas-ta` is not on PyPI for Python 3.11 arm64; removed from `ml` group. Technical indicators are implemented manually in `features/technical.py` without it.
- `torchvision` removed from `ml` group (not needed).
- `pytest-forked` added to `dev` group to handle macOS OpenMP isolation for LightGBM tests.

---

### Sub-Task 2 — Data Ingestion Layer

**Status:** `[x] complete`

**What was built**
- `data/schemas.py` — `OHLCVBar`, `Trade`, `OrderBook`, `NewsArticle`, `FundamentalSnapshot` Pydantic models with `fetch_timestamp`/`event_timestamp` separation
- `data/feeds/` — 8 feed adapters: `yfinance_feed.py`, `alpaca_feed.py`, `coingecko_feed.py`, `binance_feed.py`, `newsapi_feed.py`, `gdelt_feed.py`, `alpha_vantage_feed.py`, `sec_edgar_feed.py`
- `data/store.py` — `DataStore` wrapping SQLAlchemy with `write_bars()`, `read_bars()`, `write_news()`, `read_news()`, `write_fundamentals()`, `read_fundamentals()`
- `data/pipeline.py` — `DataPipeline` orchestrator using APScheduler

**Test coverage:** All data layer tests green (part of the 171-test suite).

---

### Sub-Task 3 — Feature Engineering Pipeline

**Status:** `[x] complete`

**What was built**
- `features/technical.py` — 40 technical indicators: EMA (9/21/50/200), MACD, ADX, Ichimoku Cloud, RSI, Stochastic, ROC, Williams %R, Bollinger Bands, ATR, Keltner Channels, HV, VWAP, OBV, Volume Z-Score, Chaikin Money Flow
- `features/statistical.py` — rolling Pearson/Spearman correlation, Engle-Granger cointegration, Johansen cointegration, spread z-scores, Ornstein-Uhlenbeck half-life
- `features/fundamental.py` — P/E z-score, EPS growth, earnings surprise magnitude, revenue surprise
- `features/sentiment.py` — FinBERT (`ProsusAI/finbert`) article scoring, exponential decay aggregation, per-ticker time-series
- `features/macro.py` — VIX level, yield curve slope, USD momentum, macro regime classification (`RISK_ON`/`RISK_OFF`/`CRISIS`)
- `features/pipeline.py` — `FeaturePipeline` with `build()`, `build_multi()`, `feature_names()`, `set_macro_cache()`
- `tests/features/` — look-ahead bias verification for all indicator groups

**Bugs fixed during implementation**
- `rolling_spearman_correlation` replaced broken pandas rolling API with explicit loop
- VWAP test had `d if False else df` name error — fixed
- OU half-life test tightened from absolute assertion to multi-seed statistical assertion
- `ou_half_life` crashes gracefully to `NaN` when `statsmodels` not installed

---

### Sub-Task 4 — ML/DL Model Layer

**Status:** `[x] complete`

**What was built**

| File | Architecture / Notes |
|---|---|
| `models/base.py` | `BaseSignalModel` ABC + `SignalOutput` dataclass (signal ∈ [-1,1], confidence ∈ [0,1], metadata dict) |
| `models/lstm_forecaster.py` | Bidirectional 2-layer LSTM, LayerNorm, AdamW, ReduceLROnPlateau, early stopping |
| `models/transformer_signal.py` | Transformer encoder with learnable CLS token + learnable positional encoding, Pre-LN, cosine LR annealing |
| `models/gaussian_process.py` | GPyTorch ExactGP, RBF × Scale kernel, type-II MLE, confidence = 1/(1+σ²) |
| `models/gradient_boosting.py` | LightGBM regressor, early stopping, SHAP TreeExplainer, top-10 SHAP values in metadata |
| `models/rl_agent.py` | PPO (stable-baselines3), `TradingEnv` Gymnasium wrapper (7 discrete actions, Sharpe-like reward, inventory tracking), scaffolded for BacktestEngine swap in Sub-Task 6 |
| `models/ensemble.py` | Ridge regression meta-learner, StandardScaler, softmax-weighted confidence blending, per-model attribution in metadata |
| `models/registry.py` | Versioned artifact registry, JSON index, `load_latest`, `load_tagged`, `best_version`, `tag_version` |
| `models/training/walk_forward.py` | `WalkForwardCV` (expanding + rolling), `evaluate_signals` (RMSE, Sharpe, max drawdown, direction accuracy, confidence-weighted Sharpe), `train_model_walk_forward` |

**Test coverage:** 56 model tests across 4 files — all passing.

**macOS OpenMP note:** LightGBM (`libomp.dylib`) conflicts with PyTorch's bundled OpenMP on macOS arm64. Tests are split across two invocations — run with `make test-models` or:
```bash
pytest tests/models/test_gradient_boosting.py tests/models/test_base.py   # LightGBM (no torch)
pytest tests/models/test_models.py tests/models/test_walk_forward.py       # PyTorch/GP/Ensemble
```
`libomp` must be installed via `brew install libomp` on macOS.

---

### Sub-Task 5 — Strategy Engine

**Status:** `[x] complete`

**What was built**

| File | Strategy / Role |
|---|---|
| `strategies/base.py` | `BaseStrategy` ABC, `Order` dataclass (validated qty/confidence), `TickerState`, `OrderSide`/`OrderType` enums |
| `strategies/momentum.py` | LSTM + Transformer ensemble, ADX trend filter (skip if ADX < 20), configurable cooldown, stop-loss/take-profit exits |
| `strategies/mean_reversion.py` | Bollinger Band z-score entry, ATR dynamic stop-loss, ADX anti-filter (skip if ADX > 25), fallback rolling z-score buffer |
| `strategies/stat_arb.py` | OLS hedge ratio, OU half-life filter, variance-ratio cointegration proxy, `PairState` container, two-leg simultaneous entry/exit |
| `strategies/market_making.py` | RL PPO quote adjustment, inventory skew (long inv → tighter ask), ATR-proportional fallback spread, `LIMIT` order posting |
| `strategies/sentiment.py` | Exponential decay-weighted FinBERT aggregation, baseline z-score normalisation, article count conviction scaling, max-hold force-close |
| `strategies/macro_factor.py` | RISK_ON/RISK_OFF/CRISIS regime via VIX + yield curve, `get_regime_multiplier()` / `get_equity_multiplier()`, earnings PEAD signal |
| `strategies/orchestrator.py` | YAML weight normalisation (sum-to-1), regime multiplier from MacroFactorStrategy, same-direction averaging, opposite-direction netting, portfolio position-cap enforcement |

**Test coverage:** 72 strategy tests across 3 files — all passing. Covers entry triggers, exit conditions (stop-loss, z-score reversion, max-hold), direction correctness, ADX filters, disabled mode, weight computation, aggregation (merge/cancel/net), position limits (scale-down, room depletion), regime multipliers.

**Cumulative test count:** 171 tests (non-model), 56 tests (models) = **227 total, 0 failures**.

---

### Sub-Task 6 — Backtesting Engine

**Status:** `[ ] pending`

**Intent**
Build an event-driven backtesting engine that simulates all strategies on historical data with realistic market microstructure assumptions (slippage, transaction costs, partial fills). This is how you validate a strategy before risking real capital.

**Expected Outcomes**
- An event-driven simulator that replays historical bars and feeds them to strategies in strict chronological order.
- Realistic order filling with configurable slippage and commission models.
- A `Portfolio` class that tracks positions, cash, and PnL per asset and per strategy in real-time.
- A full performance report covering all standard quant metrics plus per-strategy attribution.

**Todo List**
1. Define `backtesting/events.py` — event types: `BarEvent`, `OrderEvent`, `FillEvent`, `SignalEvent`, `HaltEvent`. The event queue is the spine of the simulation.
2. Implement `backtesting/engine.py` — `BacktestEngine`: loads historical data from `DataStore`, replays bar-by-bar in sorted time order, dispatches `BarEvent` to all strategies, routes `OrderEvent` to the simulated broker, and collects `FillEvent` results.
3. Implement `backtesting/broker.py` — `SimulatedBroker`: accepts `OrderEvent`, applies slippage model (fixed-percentage or half-spread), simulates limit order fill logic (only fill if price crosses limit), emits `FillEvent`.
4. Implement `backtesting/portfolio.py` — tracks cash, open positions (quantity + avg cost), realized PnL, unrealized PnL per asset and per strategy. Updates on every `FillEvent`.
5. Implement `backtesting/metrics.py` — compute: total return, CAGR, annualized Sharpe, Sortino, Calmar, max drawdown, win rate, average hold duration, profit factor, per-strategy PnL attribution.
6. Implement `backtesting/report.py` — `BacktestReport` object serializable to JSON; includes equity curve (list of portfolio values over time), trade log, and metric summary.
7. Add `backtesting/runner.py` — CLI: `python -m backtesting.runner --strategies all --start 2020-01-01 --end 2024-01-01 --tickers AAPL MSFT BTC-USD`.
8. Add walk-forward validation mode in `backtesting/walkforward.py`: rolls a training window and out-of-sample test window forward in time, training models on each fold before testing.

**Relevant Context**
- Event-driven backtesting prevents look-ahead bias by construction — a strategy only sees data up to the current simulated bar.
- Model slippage pessimistically: if mid-price is $100.00 and you buy at market, assume a fill at $100.05 (half-spread + market impact).
- The `BacktestEngine` doubles as the RL agent's training environment — once complete, update `models/rl_agent.py`'s `TradingEnv` to wrap `BacktestEngine.step()` instead of its current price-replay stub.
- The `StrategyOrchestrator.process_bar()` is the correct integration point: call it on each simulated bar and hand its returned `Order` list to the `SimulatedBroker`.

---

### Sub-Task 7 — Risk Management Layer

**Status:** `[ ] pending`

**Intent**
Add a risk management layer between strategies and execution. It enforces position limits, monitors drawdowns, computes Value-at-Risk, and can halt trading automatically. This is what separates a research toy from a serious system.

**Expected Outcomes**
- A `RiskManager` that vetoes or scales down any order violating risk constraints.
- Real-time drawdown monitoring with automatic circuit-breaker.
- Per-asset, per-strategy, and portfolio-level position limits.
- Daily VaR and CVaR computed using Historical Simulation.
- A `risk/README.md` explaining all metrics with formulas.

**Todo List**
1. Define `risk/limits.py` — `RiskLimits` config dataclass: `max_position_pct`, `max_strategy_allocation`, `max_drawdown_pct` (halt threshold), `max_daily_loss_pct`, `max_correlation_concentration` (prevents over-allocation to highly correlated assets).
2. Implement `risk/manager.py` — `RiskManager.check_order(order, portfolio) -> OrderDecision`: checks all limits, returns `APPROVE`, `SCALE_DOWN` (with new quantity), or `REJECT`. Logs every non-approval with reason and timestamp.
3. Implement `risk/var.py` — Historical VaR and CVaR: using rolling 252-day return window, compute 99% and 95% VaR (1st and 5th percentile of losses). CVaR (Conditional VaR / Expected Shortfall) = mean of losses beyond the VaR threshold.
4. Implement `risk/monitor.py` — `DrawdownMonitor` tracks peak portfolio value and current drawdown percentage. Emits `HaltTradingEvent` when `max_drawdown_pct` is breached. Also tracks daily PnL against `max_daily_loss_pct`.
5. Implement `risk/correlation.py` — computes rolling correlation matrix of all held assets. If any two assets exceed `max_correlation_concentration`, flag over-concentration.
6. Write `risk/README.md` — explains VaR, CVaR, Sharpe, Sortino, Calmar, max drawdown, and correlation concentration with formulas, intuitive explanations, and why each matters in practice.

**Relevant Context**
- CVaR (also called Expected Shortfall) is a better measure than VaR because it tells you not just "what's the worst 1% scenario threshold" but "given you're in the worst 1% of scenarios, how bad is it on average?"
- Correlation concentration is a subtle but critical risk: holding AAPL and MSFT is not twice the diversification of holding one — they are highly correlated. The risk manager should penalize concentrated bets.
- The `RiskManager.check_order()` slot sits between `StrategyOrchestrator` output and `ExecutionBroker` input. It receives the post-aggregated, weight-scaled orders from Sub-Task 5.

---

### Sub-Task 8 — Execution Layer (Paper + Live)

**Status:** `[ ] pending`

**Intent**
Build the execution layer with two adapter modes: a paper-trading adapter that simulates fills without sending real orders, and a live adapter that routes orders to Alpaca (equities) and Binance (crypto). The interface is identical — switching from paper to live is a single config value change.

**Expected Outcomes**
- `ExecutionBroker` abstract interface with `submit_order()`, `cancel_order()`, `get_positions()`, `get_account()`.
- `PaperBroker` adapter for simulation.
- `AlpacaBroker` adapter for equities.
- `BinanceBroker` adapter for crypto.
- `BrokerFactory` that returns the correct broker based on `TRADING_MODE` from config.

**Todo List**
1. Define `execution/base.py` — `ExecutionBroker` abstract class and `OrderStatus` enum: `pending`, `filled`, `partial`, `cancelled`, `rejected`.
2. Implement `execution/paper_broker.py` — fills orders immediately at last known price + configurable slippage. Tracks positions in memory. On startup, loads positions from `DataStore` for persistence across restarts.
3. Install `alpaca-trade-api` and implement `execution/alpaca_broker.py` — submits orders, polls status, streams order updates via WebSocket, maps Alpaca objects to internal `Order` / `Fill` schema.
4. Install `python-binance` and implement `execution/binance_broker.py` — submits spot market and limit orders, manages order lifecycle, maps Binance responses to internal schema.
5. Implement `execution/factory.py` — `BrokerFactory.create(mode: str) -> ExecutionBroker`. If `mode == 'live'`, assert that both API keys are present and non-empty to prevent accidental live trading with missing credentials.
6. Add integration tests in `tests/execution/` that run against paper mode and verify the full order lifecycle: submit → fill → position update → PnL calculation.

**Relevant Context**
- Alpaca's paper trading environment is indistinguishable from the live API — the same code runs in both modes.
- The `BrokerFactory` safety assertion on live mode is not paranoia — a misconfigured `.env` that accidentally sets `TRADING_MODE=live` should never silently succeed.
- The `Order` dataclass from `strategies/base.py` is the input to `ExecutionBroker.submit_order()`. No translation layer needed — the strategy output schema is already execution-ready.

---

### Sub-Task 9 — API Server

**Status:** `[ ] pending`

**Intent**
Expose the quant engine's capabilities over a REST and WebSocket API so the dashboard can consume data, trigger backtests, and monitor live state in real time.

**Expected Outcomes**
- A FastAPI server with REST endpoints for: running backtests, fetching reports, fetching positions, managing strategy configs.
- A WebSocket endpoint that streams live signals, order events, and portfolio updates.
- OpenAPI docs auto-generated at `/docs`.

**Todo List**
1. Install `fastapi`, `uvicorn[standard]`, `websockets`.
2. Define `api/schemas.py` — Pydantic request/response models for `BacktestRequest`, `BacktestResult`, `PositionSnapshot`, `LiveSignal`, `StrategyConfig`, `RiskStatus`.
3. Implement `api/routes/backtest.py` — `POST /backtest` accepts strategy name(s), tickers, date range; runs `BacktestEngine`; returns `BacktestResult` with equity curve and metrics.
4. Implement `api/routes/portfolio.py` — `GET /portfolio/positions`, `GET /portfolio/pnl`, `GET /portfolio/risk` (current VaR, drawdown).
5. Implement `api/routes/signals.py` — `GET /signals/latest` returns most recent `SignalOutput` per strategy per ticker.
6. Implement `api/routes/strategies.py` — `GET /strategies` lists all strategies and their current config; `PATCH /strategies/{id}/config` allows runtime config updates without restart.
7. Implement `api/ws/feed.py` — WebSocket at `/ws/feed` that publishes `BarEvent`, `SignalEvent`, `FillEvent`, `RiskAlert` as JSON. Clients subscribe to specific event types via a filter message.
8. Implement `api/main.py` — FastAPI app mounting all routers, configuring CORS for the dashboard origin (`localhost:5173` in dev), and starting the `DataPipeline` on startup via a lifespan context manager.

---

### Sub-Task 10 — Trading Dashboard (Frontend)

**Status:** `[ ] pending`

**Intent**
Build a React + TypeScript dashboard in `packages/dashboard/` that consumes the API server and provides a real-time view of portfolio performance, active signals, news sentiment, and strategy-level PnL. Designed as a dark-mode trading terminal.

**Expected Outcomes**
- A multi-page dashboard: Portfolio Overview, Backtest Explorer, Live Signal Monitor, News Sentiment Feed.
- Real-time price and PnL charts updating via WebSocket.
- News panel with per-headline FinBERT sentiment scores.
- Backtest UI with strategy selector, ticker selector, date range picker, and results display.
- Risk status panel showing current VaR, drawdown, and any active risk alerts.

**Todo List**
1. Set up routing with `react-router-dom` v6 — pages: `/` (overview), `/backtest`, `/live`, `/news`, `/risk`.
2. Build `components/PriceChart.tsx` — `recharts` candlestick/line chart for a selected ticker with overlaid signal markers (▲ green = buy, ▼ red = sell) and indicator overlays (EMA, Bollinger Bands).
3. Build `components/PortfolioSummary.tsx` — equity curve chart, key metric stat cards: Total Return, Sharpe, Max Drawdown, Win Rate.
4. Build `components/SignalTable.tsx` — live-updating table: ticker, strategy, direction (LONG/SHORT), signal strength (progress bar), confidence, timestamp. Color-coded by direction.
5. Build `components/NewsFeed.tsx` — scrollable list of recent `NewsArticle` records: headline, source, timestamp, FinBERT sentiment badge (green=Positive, red=Negative, grey=Neutral) with score.
6. Build `components/RiskPanel.tsx` — displays current VaR (95% and 99%), portfolio drawdown gauge, per-strategy allocation bars, and any active `RiskAlert` banners.
7. Build `pages/BacktestExplorer.tsx` — multi-select ticker input, strategy checkboxes, date range picker; calls `POST /backtest`; renders `BacktestResult` with equity curve, trade log table, and metric comparison grid.
8. Implement `hooks/useWebSocketFeed.ts` — subscribes to `/ws/feed`, filters by event type, and pushes events into Zustand store slices.
9. Implement `store/` — Zustand slices for `portfolioStore`, `signalStore`, `newsStore`, `riskStore`.
10. Apply dark mode Tailwind styling throughout — color palette: `zinc-900` background, `zinc-800` panels, `emerald-400` positive, `rose-400` negative, `sky-400` accent.

---

### Sub-Task 11 — Documentation and Learning Guide

**Status:** `[ ] pending`

**Intent**
Since this project is also a learning vehicle, add a structured documentation layer in `packages/quant-engine/docs/` that explains the math, finance theory, and CS concepts behind each component. Every concept is cross-linked to the code that implements it.

**Expected Outcomes**
- A `docs/concepts/` folder with a markdown file for each major theoretical concept.
- Top-level `README.md` at the monorepo root with setup instructions, architecture overview, and a "start here" guide.

**Todo List**
1. Write `docs/concepts/cointegration.md` — cointegration vs. correlation, Engle-Granger test, Ornstein-Uhlenbeck process, half-life calculation, pairs trading entry/exit rules with worked examples. Links to `strategies/stat_arb.py`.
2. Write `docs/concepts/market_making.md` — bid-ask spread mechanics, inventory risk, the Avellaneda-Stoikov stochastic control model, how the RL agent approximates the optimal quoting policy. Links to `strategies/market_making.py` and `models/rl_agent.py`.
3. Write `docs/concepts/reinforcement_learning.md` — Markov Decision Processes, Bellman equation, policy gradient methods, PPO algorithm walkthrough, reward shaping for trading. Links to `models/rl_agent.py`.
4. Write `docs/concepts/risk_metrics.md` — VaR, CVaR/Expected Shortfall, Sharpe, Sortino, Calmar, max drawdown, Ulcer Index — all with formulas, Python pseudocode, and intuitive explanations. Links to `risk/`.
5. Write `docs/concepts/sentiment_nlp.md` — transformer architecture overview, BERT pre-training, FinBERT fine-tuning on financial corpora, mapping softmax outputs to scalar signals, aggregation strategies. Links to `features/sentiment.py`.
6. Write `docs/concepts/technical_indicators.md` — the math behind every indicator in `features/technical.py`: RSI formula, MACD signal generation, Bollinger Band width interpretation, VWAP calculation, Ichimoku Cloud lines.
7. Write `docs/concepts/gaussian_process.md` — kernel methods, GP prior/posterior, uncertainty quantification, how GP variance controls position sizing. Links to `models/gaussian_process.py`.
8. Write `docs/concepts/macro_regimes.md` — VIX as a fear gauge, yield curve inversion as recession predictor, earnings surprise drift, how macro features interact with strategy weights. Links to `features/macro.py` and `strategies/macro_factor.py`.
9. Write root `README.md` — project overview, architecture diagram (ASCII), setup instructions (`make dev`), how to run a backtest, how to switch to live mode, link index to all concept docs.

---

## Technology Summary

| Layer | Technology | Reason |
|---|---|---|
| Language (backend) | Python 3.11+ | Industry standard for quant finance and ML |
| ML framework | PyTorch | Flexible, widely used in research and production |
| NLP/Sentiment | HuggingFace Transformers + FinBERT | Best available finance-domain NLP model |
| RL | stable-baselines3 (PPO) | Stable, well-documented, integrates with gym |
| GP model | GPyTorch | Best Gaussian Process library in PyTorch ecosystem |
| Gradient boosting | LightGBM + SHAP | Fast tabular model with built-in interpretability |
| Technical indicators | Manual implementation (pandas-ta unavailable on arm64) | Pure numpy/pandas, no C compiler issues |
| API server | FastAPI + Uvicorn | Async, fast, auto-generates OpenAPI docs |
| Database | SQLite (dev) / PostgreSQL (prod) | SQLAlchemy ORM for portability |
| Scheduling | APScheduler | Async job scheduling for multi-feed pipelines |
| Equities execution | Alpaca | Free paper trading + live execution + real-time data |
| Crypto execution | Binance | Largest crypto exchange, best API |
| Frontend | React + TypeScript + Vite | Fast, type-safe, modern |
| Charting | Recharts | React-native, composable chart library |
| Styling | Tailwind CSS + dark mode | Rapid, consistent trading terminal aesthetic |
| State management | Zustand | Lightweight, ideal for real-time event streams |

---

## Non-Goals (Phase 2)

- **Options trading** — Black-Scholes pricing, Greeks (delta, gamma, vega), options chain data via Polygon.io. A well-defined separate phase.
- **HFT / sub-millisecond latency** — the system operates on minute-to-daily bars. True HFT requires co-location and specialized infrastructure.
- **Automated hyperparameter tuning** — Bayesian optimization / AutoML. Models start with manually set hyperparameters; tuning can be layered in later.
- **Multi-user / SaaS** — single-user personal trading platform only.
