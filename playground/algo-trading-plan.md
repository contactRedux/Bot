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

**Status:** `[ ] pending`

**Intent**
Establish the monorepo directory structure, Python virtual environment, Node toolchain, dependency management, shared configuration system, and basic logging. Everything downstream depends on this foundation being clean and reproducible.

**Expected Outcomes**
- `algo-trading/` monorepo exists with the full directory layout above.
- `packages/quant-engine/` has a Python package structure and `pyproject.toml` with dependency groups.
- `packages/dashboard/` has a Vite + React + TypeScript scaffold with Tailwind CSS.
- A single root-level `.env.example` documents every API key and config variable needed across both packages.
- A root `Makefile` provides top-level developer commands (`make dev`, `make test`, `make backtest`, `make lint`).
- Environment variables are loaded via `pydantic-settings` in Python and `import.meta.env` in Vite.
- A central `config/settings.py` supports three modes: `dev`, `paper`, `live`.
- A structured logger (`structlog`) is configured and reused throughout the Python package.

**Todo List**
1. Create the root `algo-trading/` directory and initialize a git repo with a `.gitignore` covering Python, Node, and secrets.
2. Create the full `packages/quant-engine/` directory tree.
3. Set up `packages/quant-engine/pyproject.toml` with dependency groups: `core`, `data`, `ml`, `api`, `dev`.
4. Install core Python dependencies: `pandas`, `numpy`, `scipy`, `pydantic`, `pydantic-settings`, `structlog`, `httpx`, `websockets`, `APScheduler`.
5. Implement `config/settings.py` using `pydantic-settings` — reads `.env` for all API keys, DB URL, `TRADING_MODE` (`dev`/`paper`/`live`).
6. Create `config/strategy_config.yaml` — YAML file defining per-strategy parameters (lookback windows, position limits, signal thresholds) for every strategy in Sub-Task 5.
7. Scaffold `packages/dashboard/` using `npm create vite@latest` with the React + TypeScript template.
8. Install frontend dependencies: `tailwindcss`, `recharts`, `@tanstack/react-query`, `zustand`, `socket.io-client`, `react-router-dom`.
9. Configure Tailwind for dark mode (`darkMode: 'class'`).
10. Add root-level `.env.example` documenting: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `BINANCE_API_KEY`, `BINANCE_SECRET_KEY`, `NEWSAPI_KEY`, `ALPHA_VANTAGE_KEY`, `POLYGON_KEY`, `DATABASE_URL`, `TRADING_MODE`.
11. Write root `Makefile` with targets: `dev` (start API + dashboard), `test` (pytest), `backtest` (run CLI), `lint` (ruff + eslint).

**Relevant Context**
- A single `.env` at the repo root is shared between both packages — the Python backend reads it via `pydantic-settings` and Vite reads it as `VITE_*` prefixed vars.
- `pydantic-settings` is critical for preventing accidental live-mode activation: the `TRADING_MODE` env var gates whether the live broker adapter is ever instantiated.

---

### Sub-Task 2 — Data Ingestion Layer

**Status:** `[ ] pending`

**Intent**
Build a unified data ingestion system that fetches, normalizes, and stores market data (OHLCV, order book snapshots, fundamentals, news) from all data sources. All downstream feature engineering and modeling consumes data through this layer — consistency here prevents data leakage and look-ahead bias in backtests.

**Expected Outcomes**
- A `DataFeed` base class with concrete implementations for every provider.
- Historical OHLCV data fetchable for equities and crypto.
- Real-time price streaming via Alpaca WebSocket and Binance WebSocket.
- News headlines ingested from both NewsAPI and GDELT, stored with timestamps and tickers.
- Fundamental data (P/E, EPS, revenue, earnings surprise) fetchable from Alpha Vantage and SEC EDGAR.
- All data normalized to a canonical schema and stored in SQLite (dev) or PostgreSQL (prod) via `SQLAlchemy`.

**Todo List**
1. Define canonical data schemas in `data/schemas.py`: `OHLCVBar`, `Trade`, `OrderBook`, `NewsArticle`, `FundamentalSnapshot` as Pydantic models. Each record has both `fetch_timestamp` and `event_timestamp`.
2. Implement `data/feeds/yfinance_feed.py` — historical OHLCV for equities.
3. Implement `data/feeds/alpaca_feed.py` — real-time equities streaming (WebSocket) and historical bars (REST).
4. Implement `data/feeds/coingecko_feed.py` — historical and polling-based crypto OHLCV.
5. Implement `data/feeds/binance_feed.py` — real-time crypto price streaming and order book snapshots via Binance WebSocket.
6. Implement `data/feeds/newsapi_feed.py` — fetches top headlines and searches by ticker keyword; stores `NewsArticle` records.
7. Implement `data/feeds/gdelt_feed.py` — polls GDELT GKG (Global Knowledge Graph) API for financial news events; maps to `NewsArticle` schema.
8. Implement `data/feeds/alpha_vantage_feed.py` — fetches fundamental data (P/E ratio, EPS, revenue, earnings date) and stores `FundamentalSnapshot` records.
9. Implement `data/feeds/sec_edgar_feed.py` — fetches earnings filings and computes earnings surprise (actual EPS vs. consensus estimate).
10. Build `data/store.py` — `DataStore` class wrapping SQLAlchemy with `write_bars()`, `read_bars()`, `write_news()`, `read_news()`, `write_fundamentals()`, `read_fundamentals()`.
11. Add `data/pipeline.py` — `DataPipeline` orchestrator using APScheduler: runs real-time feeds continuously, polls REST feeds on configurable intervals.

**Relevant Context**
- `fetch_timestamp` vs `event_timestamp` separation is non-negotiable for backtesting correctness. A news article published at 9:30am must never be used by a strategy that is simulated at 9:25am.
- GDELT is a massive public dataset — use the streaming API endpoint rather than bulk downloads. Filter by `tone` and `themes` fields to pre-filter financial relevance.
- SEC EDGAR earnings surprise = (reported EPS − consensus estimate) / |consensus estimate|. This is a powerful short-term signal.

---

### Sub-Task 3 — Feature Engineering Pipeline

**Status:** `[ ] pending`

**Intent**
Transform raw OHLCV, fundamental, and news data into structured, ML-ready feature vectors. Good features are the single biggest driver of model performance in quantitative trading. This layer produces the inputs to all strategy models.

**Expected Outcomes**
- A `FeaturePipeline` class that accepts raw data and returns a clean, aligned feature matrix with no data leakage.
- Technical indicator features covering trend, momentum, volatility, and volume dimensions.
- Statistical features for cross-asset relationships (cointegration, correlation, spread z-scores).
- Fundamental features: valuation ratios, earnings surprise, revenue growth.
- NLP sentiment features: per-article FinBERT sentiment scores aggregated per-ticker per-time-window.
- All features computed in a walk-forward manner safe for backtesting.

**Todo List**
1. Install `pandas-ta` for technical indicators (pure Python, no C compiler required).
2. Implement `features/technical.py` — the following indicator groups, each as a function that takes a DataFrame and returns new column(s):
   - **Trend:** EMA (9, 21, 50, 200), MACD, ADX, Ichimoku Cloud
   - **Momentum:** RSI, Stochastic Oscillator, Rate of Change (ROC), Williams %R
   - **Volatility:** Bollinger Bands, ATR, Keltner Channels, Historical Volatility (HV)
   - **Volume:** VWAP, OBV (On-Balance Volume), Volume Z-Score, Chaikin Money Flow
3. Implement `features/statistical.py` — rolling pair correlations (Pearson and Spearman), Engle-Granger cointegration test scores, Johansen cointegration for baskets, spread z-scores.
4. Implement `features/fundamental.py` — normalizes `FundamentalSnapshot` data into ML features: P/E z-score vs. sector, EPS growth rate, earnings surprise magnitude, revenue surprise.
5. Install `transformers` (HuggingFace) and load `ProsusAI/finbert`.
6. Implement `features/sentiment.py` — `score_article(text) -> float` using FinBERT; `aggregate_sentiment(articles, ticker, window) -> pd.Series` returning mean score, score std dev, and article count per window.
7. Implement `features/macro.py` — derives macro-regime features: VIX level (from yfinance `^VIX`), yield curve slope (10Y − 2Y spread from FRED or Alpha Vantage), USD index momentum. These condition the strategy orchestrator's risk appetite.
8. Implement `features/pipeline.py` — `FeaturePipeline` class that chains all feature modules, aligns on a common time index, forward-fills missing values (with a capped fill limit), and returns a named feature matrix.
9. Add `tests/features/` — unit tests verifying no look-ahead bias in every feature function (assert no column uses data beyond its own row's timestamp).

**Relevant Context**
- FinBERT (`ProsusAI/finbert`) is purpose-built for financial text. It outputs `positive`, `negative`, `neutral` labels with confidence scores — map to a scalar in `[-1, +1]`.
- Ichimoku Cloud is a favourite among momentum traders — it encodes trend, support/resistance, and momentum in a single indicator.
- The macro features in `features/macro.py` are used by the Strategy Orchestrator (Sub-Task 5) to scale down overall risk exposure during high-VIX regimes.

---

### Sub-Task 4 — ML/DL Model Layer

**Status:** `[ ] pending`

**Intent**
Define, train, and serialize the machine learning models that generate trading signals. Models range from classical statistical models to deep learning architectures, each suited to a different strategy family.

**Expected Outcomes**
- A `BaseSignalModel` interface implemented by all models: `train()`, `predict()`, `save()`, `load()`.
- Five model types covering the full spectrum from interpretable to expressive:
  - **LSTM price forecaster** — sequence model for short-term return prediction.
  - **Transformer-based signal model** — attention over feature time series.
  - **Gaussian Process** — uncertainty-aware predictions for risk-proportional sizing.
  - **Gradient Boosting (XGBoost/LightGBM)** — fast, interpretable baseline for tabular features.
  - **Reinforcement Learning agent (PPO)** — models adaptive execution and market-making, Jane Street style.
- An ensemble layer that combines all model outputs.
- All models output a signal in `[-1, 1]` plus a confidence/uncertainty score.

**Todo List**
1. Set up `models/base.py` — `BaseSignalModel` abstract class with `train(X, y)`, `predict(X) -> SignalOutput`, `save(path)`, `load(path)`. Define `SignalOutput` dataclass: `signal: float`, `confidence: float`, `model_id: str`, `timestamp`.
2. Implement `models/lstm_forecaster.py` — PyTorch LSTM over a sliding window of feature vectors predicting next-period return. Include dropout, batch normalization, and a training loop with early stopping and learning rate scheduling.
3. Implement `models/transformer_signal.py` — small Transformer encoder over time-series feature sequences. Use learnable positional encoding. Output is a scalar signal via a linear projection head.
4. Implement `models/gaussian_process.py` — `gpytorch` GP regression over the feature space. Outputs mean + variance; variance is mapped to `confidence = 1 / (1 + variance)`.
5. Implement `models/gradient_boosting.py` — `LightGBM` regressor trained on the same feature matrix. Produces SHAP values alongside predictions for interpretability.
6. Implement `models/rl_agent.py` — PPO agent via `stable-baselines3`. State = feature vector + current position + unrealized PnL. Actions = `{strong_buy, buy, hold, sell, strong_sell, post_bid, post_ask}`. Reward = risk-adjusted PnL increment (Sharpe-like). Custom `gym.Env` wrapper around the backtesting engine.
7. Implement `models/ensemble.py` — meta-learner (ridge regression) trained on validation-period model outputs, producing a final blended signal and confidence.
8. Add `models/training/` — Python scripts and Jupyter notebooks for training each model: data loading, train/validation/test splits using walk-forward cross-validation (no random splits — time-series aware).
9. Add `models/registry.py` — a `ModelRegistry` that saves model artifacts (weights + metadata) to `models/artifacts/` and exposes `load_latest(model_id)`.

**Relevant Context**
- Walk-forward cross-validation is mandatory for time-series data — never use `train_test_split` with `shuffle=True` as this creates future leakage.
- LightGBM with SHAP is the "glass box" counterpart to the neural models — it tells you *which features drove this prediction*, which is critical for debugging and understanding the strategy.
- The PPO agent's custom `gym.Env` wraps the `BacktestEngine` from Sub-Task 6 — the RL training loop literally simulates the agent trading against historical data.
- Use PyTorch throughout for consistency across LSTM, Transformer, and GP models.

---

### Sub-Task 5 — Strategy Engine

**Status:** `[ ] pending`

**Intent**
Translate model signals into concrete trading decisions. Each strategy is a self-contained module that subscribes to the data pipeline, calls the relevant model(s), and emits `Order` objects. The strategy engine manages all strategies running simultaneously and aggregates their signals through a portfolio-level orchestrator.

**Expected Outcomes**
- A `BaseStrategy` class that all strategies extend.
- Six concrete strategy implementations covering the major quantitative trading paradigms.
- A `StrategyOrchestrator` that aggregates signals, applies portfolio-level weighting, and enforces risk constraints before passing orders to execution.

**Strategies Implemented**

| Strategy | Signal Source | Paradigm |
|---|---|---|
| **Momentum** | LSTM + Transformer ensemble | Trend following |
| **Mean Reversion** | Bollinger Bands + Z-score | Statistical |
| **Statistical Arbitrage (Pairs)** | Cointegration spread z-score | Market neutral |
| **Market Making** | RL agent (PPO) + order book | Liquidity provision |
| **News Sentiment** | FinBERT aggregated score | Event driven |
| **Macro Factor** | VIX regime + yield curve + fundamentals | Top-down |

**Todo List**
1. Define `strategies/base.py` — `BaseStrategy` with `on_bar(bar)`, `on_news(article)`, `on_fundamental(snapshot)`, `generate_orders() -> list[Order]`. Define `Order` dataclass: ticker, side, quantity, order_type, price, strategy_id, confidence.
2. Implement `strategies/momentum.py` — on each bar, query LSTM + Transformer ensemble. If blended signal > `entry_threshold` and confidence > `min_confidence`, emit directional market order scaled by confidence. Reverse for short. Include configurable cooldown to prevent over-trading.
3. Implement `strategies/mean_reversion.py` — track rolling z-score of price relative to Bollinger Band midline. When z-score > +2 (overbought), short. When < −2 (oversold), long. Exit at z-score = 0. Use ATR for dynamic stop-loss placement.
4. Implement `strategies/stat_arb.py` — maintain a cointegrated pair (selected by `features/statistical.py`). Track spread z-score. Enter when |z| > 2, exit when |z| < 0.5. Include half-life filter (only trade pairs whose spread mean-reverts within a tradable timeframe).
5. Implement `strategies/market_making.py` — use RL agent to determine optimal bid/ask quote offset from mid-price and order sizes. Post both sides of the book simultaneously. Adjust quotes every bar based on current inventory (skew quotes to reduce directional exposure).
6. Implement `strategies/sentiment.py` — maintain a rolling window of FinBERT sentiment scores per ticker. When score crosses a configurable z-score threshold, emit a signal. Scale position size by article count (more articles = higher conviction). Include a decay function so old articles lose influence.
7. Implement `strategies/macro_factor.py` — monitor macro regime: when VIX > 25 (fear regime), reduce all strategy allocations by 50% and shift toward mean-reversion. When yield curve inverts, flag recession risk and cut equity exposure. When earnings surprise > 2σ, emit an earnings momentum signal.
8. Implement `strategies/orchestrator.py` — receives signals from all strategies, applies per-strategy allocation weights from `strategy_config.yaml`, aggregates overlapping signals on the same ticker by averaging, enforces portfolio-level position limits, and emits a de-duplicated final order list.
9. Add comprehensive inline comments to every strategy explaining the mathematical intuition and real-world context behind each decision rule.

**Relevant Context**
- The mean reversion strategy is the statistical complement to momentum — together they cover both trending and ranging market regimes.
- The macro factor strategy acts as a "meta-strategy" that modulates the risk appetite of the whole system. When VIX spikes, position sizes across all strategies shrink.
- Stat-arb half-life is computed from the Ornstein-Uhlenbeck process fitted to the spread — the half-life tells you how many bars it typically takes for the spread to revert by 50%.

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
- The `BacktestEngine` doubles as the RL agent's training environment (wrapped as a `gym.Env` in Sub-Task 4).

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
| Technical indicators | pandas-ta | Pure Python, no C compiler needed, broad coverage |
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
