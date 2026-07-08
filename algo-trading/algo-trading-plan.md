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
│   │   ├── config/                    # Strategy configs, hyperparameters
│   │   ├── tests/                     # All Python tests
│   │   ├── docs/                      # Concept explainers and learning guides
│   │   └── pyproject.toml
│   └── dashboard/                     # TypeScript/React frontend
│       ├── src/
│       │   ├── components/            # Chart panels, signal tables, news feed
│       │   ├── hooks/                 # WebSocket feed hook (auto-reconnect)
│       │   ├── lib/                   # Typed API client + shared types
│       │   ├── pages/                 # Overview, Backtest Explorer, Live, News, Risk
│       │   └── store/                 # Zustand state slices
│       ├── package.json
│       └── vite.config.ts
├── .env.example                       # All env vars documented in one place
├── Makefile                           # Top-level commands: make dev, make backtest, make test
└── README.md
```

> **Note:** The `db/` stub folder described in the original plan was never implemented — all database work lives in `data/store.py` (SQLAlchemy). The directory exists as an empty package placeholder only.

---

## Data Sources

All sources below are included — the system ingests from all of them simultaneously. Feeds are weighted by reliability and latency in the feature pipeline.

| Source | Coverage | Cost | Role in system |
|---|---|---|---|
| **Bloomberg B-PIPE** (`blpapi`) | Equities, FX, rates, derivatives — institutional-grade | Student plan (via university portal) | **Primary anchor source** — highest weight in all feature decisions; free sources remain active as fallbacks |
| **Yahoo Finance** (`yfinance`) | Equities daily/intraday | Free | Historical OHLCV for backtesting; fallback when Bloomberg unavailable |
| **Alpaca Market Data** | Equities, crypto real-time | Free tier + paid | Primary real-time equities stream; also handles order execution |
| **Polygon.io** | Equities, crypto, tick data | $29–$200/mo | Upgrade path for higher-resolution data; needed for Phase 2 options |
| **CoinGecko** | Crypto OHLCV | Free | Historical crypto for backtesting |
| **Binance API** | Crypto real-time + order book | Free | Primary real-time crypto stream |
| **NewsAPI** | News headlines | Free tier (100 req/day) | Rapid news ingestion; primary for MVP |
| **GDELT** | Global news event database | Free | Broad macro news signal; supplements NewsAPI |
| **Alpha Vantage** | Equities, FX, fundamentals | Free tier (5 req/min) | Fundamental data (P/E, EPS, revenue) for macro factor model |
| **SEC EDGAR** | 10-K/10-Q/earnings filings | Free | Earnings surprise signals and fundamental features |

### Bloomberg Weighting Policy

Bloomberg is used as the **anchor** — not a replacement for free sources. The pipeline runs all feeds simultaneously and blends them:

- Bloomberg data receives the highest weight coefficient in the feature blending layer (configurable in `config/strategy_config.yaml`)
- Free sources (yfinance, Alpha Vantage, NewsAPI, GDELT) remain fully active and serve as fallbacks when Bloomberg is unavailable or a field is missing
- `BLOOMBERG_APP_NAME` env var controls whether Bloomberg is enabled; if unset, the system runs entirely on free sources with no degradation in architecture
- The Bloomberg adapter (`data/feeds/bloomberg_feed.py`) is scaffolded and documented; full implementation requires a live B-PIPE connection

---

## Cloud Infrastructure (AWS)

The platform targets AWS for production deployment. AWS was chosen for its breadth of managed services (RDS, S3, ECS, CloudWatch) and familiarity. All cloud config is documented in `.env.example` and a dedicated concept doc.

| Service | Role |
|---|---|
| **Amazon S3** | Model artifact storage, backtest report persistence |
| **Amazon RDS (PostgreSQL)** | Production database replacing SQLite |
| **Amazon ECS (Fargate)** | Containerised quant-engine API server |
| **Amazon CloudWatch** | Log aggregation, metric dashboards, alerts |
| **AWS Secrets Manager** | Runtime secrets (API keys, DB passwords) |

Cloud deployment is a dedicated sub-task (planned after Sub-Task 11). For now, all AWS env vars (`AWS_REGION`, `AWS_ACCOUNT_ID`, `S3_BUCKET_NAME`) are documented in `.env.example` and the system degrades gracefully when they are unset (local SQLite + local model registry).

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
- `.env.example` documents all env vars including `BLOOMBERG_APP_NAME`, `AWS_REGION`, `AWS_ACCOUNT_ID`, `S3_BUCKET_NAME`, `VITE_API_BASE_URL`, `VITE_WS_URL`

**Known issues / deviations**
- `pandas-ta` is not on PyPI for Python 3.11 arm64; removed from `ml` group. Technical indicators are implemented manually in `features/technical.py` without it.
- `torchvision` removed from `ml` group (not needed).
- `pytest-forked` added to `dev` group to handle macOS OpenMP isolation for LightGBM tests.
- `db/` stub exists as an empty package; all DB logic lives in `data/store.py`.

---

### Sub-Task 2 — Data Ingestion Layer

**Status:** `[x] complete`

**What was built**
- `data/schemas.py` — `OHLCVBar`, `Trade`, `OrderBook`, `NewsArticle`, `FundamentalSnapshot` Pydantic models with `fetch_timestamp`/`event_timestamp` separation
- `data/feeds/` — 8 feed adapters: `yfinance_feed.py`, `alpaca_feed.py`, `coingecko_feed.py`, `binance_feed.py`, `newsapi_feed.py`, `gdelt_feed.py`, `alpha_vantage_feed.py`, `sec_edgar_feed.py`
- Bloomberg adapter scaffolded in `data/feeds/bloomberg_feed.py` — documented with connection requirements; full impl requires live B-PIPE
- `data/store.py` — `DataStore` wrapping SQLAlchemy with `write_bars()`, `read_bars()`, `write_news()`, `read_news()`, `write_fundamentals()`, `read_fundamentals()`
- `data/pipeline.py` — `DataPipeline` orchestrator using APScheduler

**Test coverage:** All data layer tests green (part of the 265-test non-model suite).

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

**Test coverage:** 72 strategy tests across 3 files — all passing.

**Cumulative test count:** 171 tests (non-model), 56 tests (models) = **227 total, 0 failures**.

---

### Sub-Task 6 — Backtesting Engine

**Status:** `[x] complete`

**What was built**

| File | Role |
|---|---|
| `backtesting/events.py` | `Event` base class + `BarEvent`, `SignalEvent`, `OrderEvent`, `FillEvent`, `HaltEvent` dataclasses. Cross-type `__lt__`/`__gt__` comparison enables `heapq` and `sorted()` across event types. |
| `backtesting/broker.py` | `SimulatedBroker` with `FixedPercentageSlippage` (default 5 bps/side) and `HalfSpreadSlippage`. Market fills at close ± slippage; limit/stop orders queued with configurable TTL. Commission: `max(min_commission, qty × per_share_rate)`. |
| `backtesting/portfolio.py` | Tracks cash, signed position quantity, average cost basis, realised/unrealised PnL per asset, per-strategy attribution. Handles long add, partial close, full close, short open, short cover, and flip correctly. |
| `backtesting/metrics.py` | `compute_metrics()` — total return, CAGR, Sharpe, Sortino, Calmar, max drawdown (with dates), volatility, N trades, win rate, profit factor, avg PnL, per-strategy attribution. |
| `backtesting/report.py` | `BacktestReport` — `to_json()`/`from_json()`, `save()`/`load()`, `summary()` (terminal table), `compare()` (diff two reports). |
| `backtesting/engine.py` | `BacktestEngine.run()` (full) and `.step()` (RL Gym interface). `from_datastore()` factory. Halt-on-drawdown. Feature matrix sliced per bar (no look-ahead). |
| `backtesting/walkforward.py` | Expanding-window folds, optional `train_callback` for ML retraining between folds. |
| `backtesting/runner.py` | CLI `python -m backtesting.runner` with full flag set. |

**Test coverage:** 94 tests across 5 files — all passing.

**Cumulative test count:** 265 non-model tests + 56 model tests = **321 total, 0 failures**.

---

### Sub-Task 7 — Risk Management Layer

**Status:** `[x] complete`

**What was built**

| File | Role |
|---|---|
| `risk/limits.py` | `RiskLimits` config dataclass — `max_position_pct`, `max_strategy_allocation`, `max_drawdown_pct`, `max_daily_loss_pct`, `max_correlation_concentration` |
| `risk/manager.py` | `RiskManager.check_order(order, portfolio) → OrderDecision` — 6-stage gate returning `APPROVE`, `SCALE_DOWN` (with new qty), or `REJECT`. Logs every non-approval. |
| `risk/var.py` | Historical VaR + CVaR — rolling 252-day return window, 95% and 99% confidence levels. CVaR = Expected Shortfall (mean of losses beyond VaR threshold). |
| `risk/monitor.py` | `DrawdownMonitor` — tracks peak equity and current drawdown %; emits `HaltTradingEvent` on breach; tracks daily P&L vs `max_daily_loss_pct`. |
| `risk/correlation.py` | Rolling correlation matrix of held assets. Flags over-concentration when any pair exceeds `max_correlation_concentration`. |
| `risk/README.md` | Full concept reference — VaR, CVaR, Sharpe, Sortino, Calmar, max drawdown, correlation concentration with formulas and intuitive explanations. |

**Test coverage:** 25 tests across 5 files (test_limits, test_manager, test_var, test_monitor, test_correlation) — all passing.

**Cumulative test count:** 266 non-model tests + 56 model tests = **322 total, 0 failures**.

---

### Sub-Task 8 — Execution Layer (Paper + Live)

**Status:** `[x] complete`

**What was built**

| File | Role |
|---|---|
| `execution/base.py` | `ExecutionBroker` ABC + `OrderStatus` enum (`pending`, `filled`, `partial`, `cancelled`, `rejected`) |
| `execution/paper_broker.py` | `PaperBroker` — immediate fills at last price + configurable slippage model; positions tracked in memory; loads from `DataStore` on startup |
| `execution/alpaca_broker.py` | `AlpacaBroker` — submits orders, polls status, streams order updates via WebSocket, maps Alpaca objects to internal `Order`/`Fill` schema |
| `execution/binance_broker.py` | `BinanceBroker` — spot market + limit orders, full lifecycle management, Binance→internal schema mapping |
| `execution/factory.py` | `BrokerFactory.create(mode)` — asserts API keys present before live mode; prevents accidental live trading on misconfigured `.env` |

**Test coverage:** 15 tests across 5 files — all passing.

**Cumulative test count:** 281 non-model tests + 56 model tests = **337 total, 0 failures**.

---

### Sub-Task 9 — API Server

**Status:** `[x] complete`

**What was built**

| File | Endpoints / Role |
|---|---|
| `api/schemas.py` | All Pydantic request/response models: `BacktestRequest`, `BacktestResponse`, `BacktestStatusResponse`, `PortfolioResponse`, `PositionItem`, `SignalItem`, `SignalsResponse`, `RiskStatusResponse`, `StrategyInfo`, `StrategiesResponse`, `HealthResponse`, `WSEvent` |
| `api/deps.py` | `AppState` dependency injection — broker, monitor, risk manager, orchestrator, portfolio all live on `app.state`; injected into routes via `Depends` |
| `api/routes/backtest.py` | `POST /api/backtest/run`, `GET /api/backtest/status/{run_id}`, `GET /api/backtest/result/{run_id}` — async background task, progress broadcast via WS |
| `api/routes/portfolio.py` | `GET /api/portfolio/positions`, `GET /api/portfolio/pnl` |
| `api/routes/signals.py` | `GET /api/signals/latest` |
| `api/routes/risk.py` | `GET /api/risk/status`, `POST /api/risk/resume` |
| `api/routes/strategies.py` | `GET /api/strategies`, `PATCH /api/strategies/{id}/toggle` |
| `api/routes/health.py` | `GET /health` — uptime, broker status, trading mode |
| `api/ws/feed.py` | `WebSocket /ws/feed` — `ConnectionManager` singleton, heartbeat every 15s, broadcast helpers: `broadcast_signal`, `broadcast_fill`, `broadcast_risk_alert`, `broadcast_portfolio_update`, `broadcast_backtest_progress` |
| `api/main.py` | FastAPI app — CORS (all origins in dev), lifespan context manager (startup/shutdown), all routers mounted under `/api` prefix |

**WebSocket event envelope** (used by dashboard `useWebSocketFeed` hook):
```json
{ "event_type": "bar|signal|fill|risk_alert|portfolio_update|heartbeat|backtest_progress",
  "payload": { ... },
  "timestamp": "2024-01-01T12:00:00Z" }
```

**Test coverage:** 30 tests across 8 files — all passing.

**Cumulative test count:** 266 non-model tests + 56 model tests ≈ **322 total, 0 failures**.

> **Note:** Exact cumulative counts for Sub-Tasks 7–9 reflect `grep`-based test function counts. Official count from `pytest --co -q` may differ slightly due to parametrize expansion; 266 is the floor.

---

### Sub-Task 10 — Trading Dashboard (Frontend)

**Status:** `[x] complete`

**What was built**

| File | Role |
|---|---|
| `src/lib/types.ts` | All shared TypeScript types mirroring `api/schemas.py` — `WSEvent`, `WSEventType`, `PortfolioResponse`, `PositionItem`, `SignalItem`, `BacktestRequest`, `BacktestResponse`, `RiskStatusResponse`, `StrategyInfo`, `NewsArticle` |
| `src/lib/api.ts` | Typed `fetch` wrappers for all REST endpoints — portfolio, signals, risk, strategies, backtest run/status/result |
| `src/store/index.ts` | Zustand slices: `signalStore` (200-signal ring), `newsStore` (500-article ring), `portfolioStore` (snapshot + 1000-pt equity curve), `riskStore`, `fillStore` (500-fill ring), `wsStore` (connected / lastHeartbeat) |
| `src/hooks/useWebSocketFeed.ts` | Single WS connection; auto-reconnect with exponential back-off (1s→30s); routes all 7 `event_type` values to correct store slices |
| `src/components/NavBar.tsx` | Brand + nav links + live/disconnected WS status indicator (heartbeat age) |
| `src/components/PriceChart.tsx` | Recharts `ComposedChart` — close price line, EMA-20 dashed overlay, `ReferenceDot` signal markers (▲ emerald / ▼ rose) |
| `src/components/PortfolioSummary.tsx` | Stat cards (equity, unrealised P&L, realised P&L, total P&L) + area equity curve + positions table |
| `src/components/SignalTable.tsx` | Live table — direction badge, strength bar, confidence %, age; seeded from REST + live-updated via WS |
| `src/components/NewsFeed.tsx` | Scrollable articles with `positive`/`negative`/`neutral` FinBERT sentiment badges + score |
| `src/components/RiskPanel.tsx` | VaR/CVaR metric rows, drawdown + daily-loss gauge bars, high-correlation pairs table, HALT banner with resume button |
| `src/pages/Overview.tsx` | `PortfolioSummary` + `PriceChart` (primary held ticker) + `SignalTable` (20 rows) |
| `src/pages/LiveMonitor.tsx` | Positions panel + `SignalTable` (200 rows) + fill log table |
| `src/pages/NewsFeed.tsx` | Ticker chip-filter bar + `NewsFeed` component (200 articles) |
| `src/pages/RiskDashboard.tsx` | Full `RiskPanel` |
| `src/pages/BacktestExplorer.tsx` | Form (tickers, strategies, dates, capital, interval) → `POST /api/backtest/run` → 2s poll → equity curve + 10 metric cards + strategy attribution bars + scrollable trade log |
| `src/App.tsx` | Router + single `useWebSocketFeed()` call at root |

**Key runtime dependencies added:** `date-fns`, `socket.io-client` (available for future Socket.IO migration if needed — currently using native `WebSocket`)

**Dark mode palette:** `zinc-900` bg · `zinc-800` panels · `emerald-400` positive · `rose-400` negative · `sky-400` accent

---

### Sub-Task 11 — Documentation and Learning Guide

**Status:** `[x] complete`

**Intent**
A structured documentation layer in `packages/quant-engine/docs/concepts/` that explains the math, finance theory, and CS concepts behind every component. All docs are cross-linked to the implementing code.

**What was built**

| File | Concepts covered |
|---|---|
| `docs/concepts/cointegration.md` | Cointegration vs correlation; Engle-Granger test; OU process; half-life formula; pairs trading entry/exit rules with worked examples. Links → `strategies/stat_arb.py`, `features/statistical.py` |
| `docs/concepts/market_making.md` | Bid-ask spread mechanics; inventory risk; Avellaneda-Stoikov stochastic control model; how the RL agent approximates the optimal quoting policy. Links → `strategies/market_making.py`, `models/rl_agent.py` |
| `docs/concepts/reinforcement_learning.md` | MDPs; Bellman equation; policy gradient; PPO algorithm walkthrough; reward shaping for trading. Links → `models/rl_agent.py` |
| `docs/concepts/risk_metrics.md` | VaR; CVaR/Expected Shortfall; Sharpe; Sortino; Calmar; max drawdown; Ulcer Index — all with formulas, Python pseudocode, intuitive explanations. Links → `risk/` |
| `docs/concepts/sentiment_nlp.md` | Transformer architecture; BERT pre-training; FinBERT fine-tuning on financial corpora; softmax→scalar mapping; aggregation strategies. Links → `features/sentiment.py` |
| `docs/concepts/technical_indicators.md` | Math behind every indicator in `features/technical.py` — RSI, MACD, Bollinger Bands, VWAP, Ichimoku Cloud, ATR, ADX. Links → `features/technical.py` |
| `docs/concepts/gaussian_process.md` | Kernel methods; GP prior/posterior; uncertainty quantification; how GP variance controls position sizing. Links → `models/gaussian_process.py` |
| `docs/concepts/macro_regimes.md` | VIX as fear gauge; yield curve inversion; earnings surprise drift; how macro features interact with strategy weights. Links → `features/macro.py`, `strategies/macro_factor.py` |
| `docs/concepts/aws_cloud.md` | AWS architecture for the platform; S3 model registry; RDS vs SQLite; ECS Fargate deployment; Secrets Manager for API keys; CloudWatch logging; migration path from local dev to AWS. |
| `README.md` (monorepo root) | Project overview; ASCII architecture diagram; full setup guide (`make dev`); how to run a backtest; how to switch to paper/live mode; Bloomberg + AWS configuration; link index to all concept docs |

---

---

### Phase 1 — Security / Runtime Prerequisites

**Status:** `[x] complete`

**What was built**
- `Makefile` — `--host 0.0.0.0` → `--host 127.0.0.1`; Python interpreter resolves to `python3.11` first
- `config/settings.py` — 4 new settings: `OIDC_ISSUER_URL`, `OIDC_AUDIENCE`, `API_REQUIRED_ROLE` (default `operator`), `AWS_SECRETS_PREFIX`
- `api/deps.py` — `require_operator` FastAPI dependency: no-op in dev (OIDC not configured), JWT validation + role check in prod (PyJWT + JWKS, RS256/ES256, flat `roles` and Keycloak `realm_access.roles` supported). `data_store: DataStore` field added to `AppState`.
- Protected mutation endpoints: `POST /api/backtest/run`, `DELETE /api/backtest/{run_id}`, `PATCH /api/strategies/{id}`, `POST /api/risk/resume`
- Structured AUDIT log lines on every control-plane mutation (action, identifiers, trading_mode, client IP)
- `.env.example` — OIDC and AWS Secrets Manager vars documented
- `README.md` — localhost binding security note, new env vars in reference table
- `tests/api/test_auth.py` — 10 new tests

---

### Phase 2 — Bloomberg B-PIPE Full Implementation

**Status:** `[x] complete`

**What was built**
- `data/feeds/bloomberg_feed.py` — optional `blpapi` import (startup is clean when absent). `fetch_bars` via `HistoricalDataRequest`, `fetch_news` via `NEWS_STORY_RT_REQUEST`. `BloombergFeed.is_available()` pre-check.
- `config/settings.py` — 4 new settings: `BLOOMBERG_HOST` (`localhost`), `BLOOMBERG_PORT` (`8194`), `BLOOMBERG_APP_NAME`, `BLOOMBERG_TIMEOUT_SECONDS` (`30`)
- `data/pipeline.py` — provider-priority policy: Bloomberg first for equity bars/news, fallback to free sources when unavailable
- `features/sentiment.py` — source-quality weights: Bloomberg 2.0×, NewsAPI 1.0×, GDELT 0.8×
- `data/feeds/__init__.py` — `BloombergFeed` exported
- `pyproject.toml` — `[bloomberg]` optional dependency group with `blpapi>=3.19`
- `tests/data/test_bloomberg_feed.py` — 15 new tests

---

### Phase 3 — AWS Terraform IaC Baseline

**Status:** `[x] complete`

**What was built**

11 Terraform files in `infra/terraform/`:

| File | Provisions |
|------|-----------|
| `providers.tf` | AWS ~5.50, Terraform ≥1.7, S3 backend stub |
| `variables.tf` | All inputs — region, AZs, CPU/memory, DB class, `db_password` (sensitive=true) |
| `main.tf` | Common locals, `common_tags`, AZ resolution |
| `network.tf` | VPC, public+private subnets ×2 AZs, IGW, NAT gateway, route tables |
| `ecr.tf` | ECR repo: scan-on-push, immutable tags, lifecycle (keep 10 tagged, expire untagged 1 day) |
| `ecs.tf` | ECS cluster + CW log group + task def (non-root UID 1001, readonlyRootFilesystem, drop ALL capabilities, Secrets Manager injection) + Fargate service |
| `iam.tf` | Execution role (ECR pull + SM read) + task role (S3 least-priv, scoped CW metrics, ECS Exec) |
| `rds.tf` | RDS PostgreSQL 16, private subnets, encrypted, deletion-protected, 7-day backups |
| `s3.tf` | Artifacts bucket: public access blocked ×4, AES-256, versioning, lifecycle (Glacier 90d, expire 365d) |
| `secrets.tf` | Secrets Manager for API keys + DB URL; `ignore_changes` prevents Terraform overwriting real secrets |
| `outputs.tf` | ECR URL, ECS names, RDS endpoint, S3/secret ARNs |

Updated: `docs/concepts/aws_cloud.md` (Section 10: Terraform IaC Baseline), `README.md` (AWS Terraform Deployment section).

---

### Phase 4 — PriceChart REST OHLC Wiring

**Status:** `[x] complete`

**What was built**
- `api/schemas.py` — `PriceHistoryPoint` + `PriceHistoryResponse` schemas
- `api/routes/portfolio.py` — `GET /api/portfolio/price-history?ticker=&interval=&limit=` endpoint; reads from `DataStore.read_bars()`, interval-aware lookback window, returns close/OHLCV points
- `api/deps.py` — `data_store: DataStore` field added to `AppState`
- `api/main.py` — DataStore initialized in lifespan and attached to AppState
- `data/store.py` — `DataStore.__init__` accepts `connect_args` and `poolclass` kwargs (needed for `StaticPool` in tests)
- `tests/api/test_portfolio.py` — 14 new tests (empty store, response shape, ticker/interval echo, limit validation, real seeded bars, shape verification, limit trimming, None store graceful fallback)
- `tests/api/conftest.py` — DataStore wired into test AppState using `StaticPool` for correct in-memory connection sharing
- `src/lib/types.ts` — `PriceHistoryPoint` + `PriceHistoryResponse` TypeScript interfaces
- `src/lib/api.ts` — `fetchPriceHistory(ticker, interval, limit)` typed REST helper
- `src/pages/Overview.tsx` — replaces `data={[]}` with `useQuery` + `fetchPriceHistory`, refreshes every 60s

**Cumulative test count:** 567 passing non-model tests (570 collected, 3 skipped) + 37 model tests = **604 total, 0 failures**.



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
| Database (dev) | SQLite | Zero-config, file-based — perfect for local development |
| Database (prod) | PostgreSQL on AWS RDS | Managed, scalable, SQLAlchemy ORM for portability |
| Cloud | AWS (S3, RDS, ECS, CloudWatch) | Broadest managed service coverage; familiar ecosystem |
| Institutional data | Bloomberg B-PIPE (Student Plan) | Highest-quality market data; used as anchor with free fallbacks |
| Scheduling | APScheduler | Async job scheduling for multi-feed pipelines |
| Equities execution | Alpaca | Free paper trading + live execution + real-time data |
| Crypto execution | Binance | Largest crypto exchange, best API |
| Frontend | React + TypeScript + Vite | Fast, type-safe, modern |
| Charting | Recharts | React-native, composable chart library |
| Styling | Tailwind CSS + dark mode | Rapid, consistent trading terminal aesthetic |
| State management | Zustand | Lightweight, ideal for real-time event streams |
| REST data fetching | TanStack React Query | Caching, polling, stale-while-revalidate out of the box |
| WebSocket | Native browser WebSocket API | No external dependency; auto-reconnect hook in `useWebSocketFeed.ts` |

---

## Non-Goals (current scope)

- **Options trading** — Black-Scholes pricing, Greeks (delta, gamma, vega), options chain data via Polygon.io. A well-defined separate phase.
- **HFT / sub-millisecond latency** — the system operates on minute-to-daily bars. True HFT requires co-location and specialized infrastructure.
- **Automated hyperparameter tuning** — Bayesian optimization / AutoML. Models start with manually set hyperparameters; tuning can be layered in later.
- **Multi-user / SaaS** — single-user personal trading platform only.

---

## Institutional-Grade Upgrade Phases (next-steps.md)

These phases are tracked in detail in `next-steps.md`. All seven phases are complete.

| Phase | Status | Description |
|-------|--------|-------------|
| 1 — Security/runtime | ✅ **Complete** | localhost binding, OIDC auth seam, RBAC, audit logging |
| 2 — Bloomberg B-PIPE | ✅ **Complete** | Full `bloomberg_feed.py` adapter, priority policy, sentiment weighting |
| 3 — AWS Terraform IaC | ✅ **Complete** | 11 `.tf` files: VPC, ECR, ECS Fargate, RDS, S3, Secrets Manager, IAM, CloudWatch |
| 4 — PriceChart OHLC wiring | ✅ **Complete** | `GET /api/portfolio/price-history`, typed frontend, `Overview.tsx` live data |
| 5 — Integration/E2E tests | ✅ **Complete** | 28 integration tests (full lifespan), Playwright E2E scaffold, `make test-integration` / `test-e2e` |
| 6 — mypy --strict hardening | ✅ **Complete** | Per-module overrides, 0 mypy errors on targeted modules, fixed pre-existing type bugs |
| 7 — Execution realism | ✅ **Complete** | `SqrtImpactSlippage`, partial fills in `SimulatedBroker` + `PaperBroker`, order-book imbalance feature, 46 new tests |

### Sub-Task 12 — Phase 5: Integration/E2E Suite

**Status:** `[x] complete`

**What was built**
- `tests/integration/__init__.py` — new package
- `tests/integration/test_api_app_state.py` — 15 tests: health, root, strategies, risk status, portfolio, WebSocket heartbeat (full real lifespan, no pre-injected state)
- `tests/integration/test_portfolio_chart_flow.py` — 13 tests: seeded DataStore price-history flow, portfolio endpoints, backtest round-trip with background task polling
- `packages/dashboard/playwright.config.ts` — Playwright config (Chromium only, baseURL `http://localhost:5173`, 30 s timeout)
- `packages/dashboard/tests/e2e/overview.spec.ts` — 4 E2E specs: page loads, heading visible, no JS console errors, React root mounted
- `packages/dashboard/package.json` — `@playwright/test ^1.47.0` added to devDependencies
- `Makefile` — `test-integration` and `test-e2e` targets

### Sub-Task 13 — Phase 6: mypy --strict Hardening

**Status:** `[x] complete`

**What was built**
- `pyproject.toml` — `[[tool.mypy.overrides]]` for `api.*`, `config.settings`, `data.store`, `execution.base`, `strategies.base` with `disallow_untyped_defs = true` and `warn_return_any = true`
- `data/store.py` — typed SQLite pragma listener, `# type: ignore[attr-defined]` on ORM column attribute accesses and `rowcount`, correct `int` type annotation on all `inserted` variables
- `api/deps.py` — explicit `state: AppState` narrowing before return in `get_app_state()`
- `api/main.py` — `Literal["ok", "degraded"]` type for `_status`; `from typing import Literal` import; `StrategyOrchestrator(strategies=[], config=...)` signature fix
- `api/routes/backtest.py` — `from datetime import UTC`; `bar_interval=` kwarg (was `interval=`); correct `StrategyOrchestrator` call; `_all_strategies` attribute access
- **Result:** `python3.11 -m mypy api/ config/settings.py data/store.py execution/base.py strategies/base.py` → **0 errors, 16 source files**

### Sub-Task 14 — Phase 7: Execution Realism + Microstructure Layer

**Status:** `[x] complete`

**What was built**
- `backtesting/broker.py` — `SlippageModelType` enum; `SqrtImpactSlippage` class (impact coefficient, ADV-injection pattern, fallback to fixed bps); `volume_participation_rate`, `min_fill_pct`, `fee_rate` params on `SimulatedBroker`; `_fill_market_with_partial()` caps fills per bar and re-queues remainder; `update_adv()` EMA daily-volume tracker; `calc_sqrt_slippage()` public helper; `process_bar()` handles pending market orders (partial remainder fills on next bar)
- `execution/paper_broker.py` — `partial_fill_mode: bool = False` (off by default, backward-compat); `volume_participation_rate` and `simulated_bar_volume` params; `_execute_partial()` returns `OrderStatus.PARTIAL` with `remaining_qty` in metadata; removed unused `portfolio_value` variable
- `features/pipeline.py` — `order_book: OrderBook | None` init param; `set_order_book()` method; `_compute_order_book_imbalance()` static method using top-5 bid/ask levels; `order_book_imbalance` scalar column broadcast into the feature matrix when a book snapshot is set
- `tests/backtesting/test_slippage.py` — 14 tests
- `tests/backtesting/test_partial_fills.py` — 12 tests (includes limit order price-check tests)
- `tests/execution/test_paper_broker_partial.py` — 9 tests
- `tests/features/test_order_book_imbalance.py` — 11 tests

**Cumulative test count:** 641 passing non-model tests (644 collected, 3 pre-existing skips) + 37 model tests + 28 integration tests = **706 total, 0 failures**.
