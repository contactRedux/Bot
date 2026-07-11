# 📈 Algorithmic AI Trading Platform

A full-stack, research-to-execution algorithmic trading platform built with Python 3.11, FastAPI, and React/TypeScript. Nine independent strategy engines, a live data pipeline, ML models, a complete risk layer, backtesting with Bayesian optimisation, and an Apple-style dashboard — all in a single monorepo.

---

## 🎯 Project Purpose

This platform was built to demonstrate end-to-end quantitative engineering: from raw market data through feature engineering, ML model training, multi-strategy signal generation, risk-gated execution, and real-time operator visibility — all the way to an LLM-powered analyst that explains in plain English what the bot is doing and why.

Instead of scattering research notebooks, broker scripts, risk checks, and monitoring tools across disconnected systems, this codebase is one coherent platform that can evolve from paper trading on a laptop to a cloud-deployed live system.

This is the kind of stack that underpins hedge funds, prop trading desks, and quant firms at their core.

---

## 🏗 Architecture

```
Bloomberg / Alpaca / Binance / NewsAPI / GDELT / yfinance / Alpha Vantage
        │
        ▼  data/feeds/  (DataPipeline — APScheduler: backfill + live streaming)
   DataStore  (SQLite in dev  →  PostgreSQL on AWS RDS)
        │
        ▼  features/pipeline.py
   Feature matrix  (40+ technical indicators · FinBERT sentiment · macro · order-book)
        │
        ├──▶  models/   (LSTM · Transformer · GP · LightGBM · PPO RL · Ensemble)
        │
        ▼  strategies/  (9 strategy classes  →  StrategyOrchestrator)
   Aggregated orders
        │
        ▼  risk/manager.py
   Risk-gated orders  (APPROVE · SCALE_DOWN · REJECT)
        │
        ▼  execution/
   PaperBroker (dev/paper)  OR  AlpacaBroker / BinanceBroker (live)
        │
        ▼  api/ws/feed.py
   React Dashboard  (real-time WebSocket + REST)

AI Analyst path (on-demand):
   AppState  (portfolio · trades · technicals · signals · news · risk)
        │
        ▼  api/routes/ai_analyst.py
   OpenAI GPT-4o  OR  Anthropic Claude  (or free offline fallback)
        │
        ▼  Structured analyst report  →  /ai dashboard page
```

---

## 📦 Components at a Glance

| Component | Stack | Key Responsibilities |
|-----------|-------|---------------------|
| **quant-engine** | Python 3.11, FastAPI, SQLAlchemy | Data, features, ML, strategies, risk, execution, REST+WS API |
| **dashboard** | React 18, TypeScript, Vite, Tailwind | 12-page trading terminal, Apple-style UI |
| **DataPipeline** | APScheduler, aiohttp | Multi-source data ingestion, backfill, live streaming |
| **StrategyOrchestrator** | NumPy, Pandas | Aggregates 9 strategies, applies risk-budget weights |
| **RiskManager** | SciPy | VaR/CVaR, drawdown monitor, daily-loss circuit-breaker |
| **BacktestEngine** | Pandas, NumPy | Look-ahead-safe event loop, partial fills, √N slippage |
| **Bayesian Optimiser** | Optuna TPE | Finds best strategy parameters across 5 tuneable strategies |
| **AI Analyst** | OpenAI / Anthropic | Plain-English report from live system state |
| **TradingEngine** | asyncio | Live paper/live loop: bars → features → signals → orders |
| **AWS Terraform** | Terraform | VPC · ECS Fargate · RDS PG16 · ECR · S3 · Secrets Manager |

---

## 🧠 Strategy Reference

| Strategy | Class | Signal Logic | Best Regime |
|----------|-------|-------------|-------------|
| **Momentum** | `MomentumStrategy` | LSTM+Transformer ensemble; technical fallback (EMA cross, RSI, MACD, SMA trend) when no models loaded | Trending |
| **Mean Reversion** | `MeanReversionStrategy` | Bollinger Band z-score; ATR stop | Ranging |
| **Stat Arb** | `StatArbStrategy` | Engle-Granger cointegration; spread z-score; OU half-life filter | Uncorrelated pairs |
| **Market Making** | `MarketMakingStrategy` | Avellaneda-Stoikov RL-adjusted quotes; inventory skew | High-volume, tight spreads |
| **Sentiment** | `SentimentStrategy` | FinBERT article sentiment z-score; decay weighting | Event-driven |
| **Macro Factor** | `MacroFactorStrategy` | VIX regime × yield curve × earnings surprise multipliers | Regime change |
| **Kalman Trend** | `KalmanTrendStrategy` | 1D Kalman filter; trades normalised innovation ν/√S | Any trending |
| **Kelly + Vol Target** | `KellyVolStrategy` | Fractional Kelly sizing; vol targeting scales position by σ_target/σ_realised (Moreira & Muir 2017) | Low-vol |
| **VWAP Reversion** | `VWAPReversionStrategy` | VWAP deviation %; ATR volatility filter; volume confirmation | Intraday |

All strategies are configurable via `config/strategy_config.yaml` and togglable at runtime via `PATCH /api/strategies/{id}` or the Strategies dashboard page. The momentum strategy automatically falls back to a 4-indicator technical signal (EMA cross, RSI, MACD, SMA alignment) when no ML models are loaded, enabling paper trading out of the box.

---

## 🖥 Dashboard Pages

| Route | Page | What it shows |
|-------|------|--------------|
| `/` | **Overview** | Equity curve, portfolio stat cards, live positions, recent signals |
| `/chart` | **Charts** | Apple-style area chart; editable watchlist (localStorage); 1D/1W/1M/3M/1Y/ALL ranges; SMA/EMA/BB overlays; paper trade panel |
| `/watchlist` | **Watchlist** | Persistent price-alert watchlist; live prices; above/below threshold alerts |
| `/analysis` | **Ticker Analysis** | Composite technical rating (Strong Buy→Strong Sell) for any ticker; 10-indicator signal bars; Wall St. analyst consensus from yfinance; price targets |
| `/ai` | **AI Analyst** | GPT-4o / Claude analyst report from live system data; offline rule-based fallback; session history |
| `/metrics` | **Metrics** | Sharpe, Sortino, Calmar, max drawdown, CAGR, win rate, profit factor, per-strategy PnL attribution |
| `/live` | **Live Monitor** | Real-time strategy signals (WS); fill log; open positions P&L |
| `/bot` | **Bot Analysis** | Engine watchlist: per-ticker technical rating, Wall St. consensus bar, upside-to-target, engine signal, expandable indicator breakdown |
| `/backtest` | **Backtest Explorer** | Single backtest + equity curve; Bayesian optimisation (Optuna TPE, trial chart); walk-forward OOS validation |
| `/strategies` | **Strategies** | Engine start/stop; trading mode; per-strategy enable/disable toggles; loop counter |
| `/news` | **News Feed** | Live pull from NewsAPI + GDELT; keyword sentiment fallback; article attribution across 80+ ticker aliases |
| `/risk` | **Risk Dashboard** | VaR/CVaR gauges; drawdown; daily loss %; correlation heatmap; halt state |

---

## ✅ Prerequisites

| Tool | Version | Purpose | Install |
|------|---------|---------|---------|
| **Python** | 3.11+ | Backend engine | [python.org](https://python.org) |
| **Node.js** | 20+ | Dashboard dev server | [nodejs.org](https://nodejs.org) |
| **libomp** | Any | LightGBM on macOS | `brew install libomp` |
| **PostgreSQL** | 15+ | Production DB (optional — SQLite used in dev) | [postgresql.org](https://postgresql.org) |

> Python and Node are the only hard requirements to run the full platform locally. PostgreSQL is optional — the system falls back to SQLite automatically.

---

## 🚀 Quick Start

### Step 1 — Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/algo-trading.git
cd algo-trading
```

### Step 2 — Set up environment variables

```bash
cp .env.example .env
```

Open `.env`. The minimum required to run in dev mode (no keys needed at all):

```bash
TRADING_MODE=dev       # dev, paper, or live
```

Optional keys that unlock additional features:

```bash
LLM_API_KEY=sk-...     # GPT-4o or Claude; leave blank for free offline mode
NEWSAPI_KEY=...        # Expands news coverage (free tier: 100 req/day)
ALPACA_API_KEY=...     # Required for paper/live equity trading
```

### Step 3 — Install dependencies

```bash
make install
```

This runs `pip install -e ".[data,ml,api,dev]"` in the quant-engine and `npm install` in the dashboard.

To also install LLM client libraries for the AI Analyst:

```bash
cd packages/quant-engine
pip install openai          # for GPT-4o / GPT-4o-mini
pip install anthropic       # for Claude
```

### Step 4 — Start everything

```bash
make dev
```

This starts both servers in parallel:

| Server | URL | Purpose |
|--------|-----|---------|
| FastAPI | `http://127.0.0.1:8000` | REST + WebSocket API (localhost only) |
| Swagger docs | `http://127.0.0.1:8000/docs` | Interactive API explorer |
| Dashboard | `http://localhost:5173` | React trading terminal |

> **Security note:** The API binds to `127.0.0.1` (loopback) by default. Never use `--host 0.0.0.0` in development — it exposes unauthenticated control-plane endpoints on all interfaces. In production, place the API behind an AWS ALB or nginx reverse proxy and set `OIDC_ISSUER_URL` to require Bearer token auth on all mutation endpoints.

---

## 📊 Running a Backtest

**Via Dashboard** — open `/backtest`, choose tickers, date range, and one of three modes:

| Panel | What it does |
|-------|-------------|
| **Run Backtest** | Single simulation with equity curve, trade log, and metrics |
| **Bayesian Optimisation** | Finds best parameters for a strategy via Optuna TPE (5–200 trials) |
| **Walk-Forward Validation** | Expanding-window OOS test across N folds — the proper overfitting check |

**Via CLI:**

```bash
make backtest ARGS="--strategies momentum mean_reversion \
  --tickers AAPL MSFT NVDA \
  --start 2022-01-01 --end 2024-12-31 \
  --capital 100000 \
  --output reports/result.json"
```

**Example result** (Momentum + Mean Reversion on AAPL/MSFT/NVDA, 2022–2024, which included the 2022 bear market):

| Metric | Value |
|--------|-------|
| Final equity | $102,620 |
| Total return | +2.62% |
| Max drawdown | −2.75% |
| Total trades | 237 |
| Win rate | 29.5% |
| Profit factor | 1.28× |
| Mean Reversion P&L | +$2,887 |

---

## 🤖 AI Analyst

The AI Analyst (`/ai`, `POST /api/ai/analyse`) sends live system state to an LLM and returns a structured plain-English report. Works with no API key in offline mode.

### Data sent to the LLM

| Data | Source |
|------|--------|
| Portfolio state (cash, equity, positions, unrealised P&L) | Live `Portfolio` |
| Recent trades (ticker, side, fill price, strategy) | `PaperBroker.fills` |
| Technical analysis per ticker (RSI, MACD, SMA-200, composite rating) | `api/routes/analysis.py` |
| Active strategy signals (what each strategy currently sees) | `AppState.latest_signals` |
| Risk state (drawdown %, halt flag, VaR) | `DrawdownMonitor` |
| News headlines from the last 48 h with sentiment scores | `DataStore.read_news()` |

### Report structure

| Section | Content |
|---------|---------|
| **Executive Summary** | 1–2 sentence current state of the system |
| **Market Commentary** | Technical conditions — RSI levels, trend direction, price vs SMA-200 |
| **Trade Rationale** | Why the bot made each recent trade — cites strategy name and signal values |
| **Risk Assessment** | Current drawdown, concentration, halt state |
| **Outlook** | What conditions would trigger more trades; what to monitor |
| **Key Points** | Bullet list for quick scanning |

### Configuration

```bash
# .env
LLM_PROVIDER=openai                         # "openai" or "anthropic"
LLM_API_KEY=sk-...                          # leave blank for free offline mode
LLM_MODEL=gpt-4o                            # or gpt-4o-mini / claude-3-5-sonnet-20241022
LLM_MAX_TOKENS=1200
```

| Provider | Default model | Fast/cheap alternative |
|----------|--------------|----------------------|
| OpenAI | `gpt-4o` | `gpt-4o-mini` |
| Anthropic | `claude-3-5-sonnet-20241022` | `claude-3-haiku-20240307` |

**No API key:** Leave `LLM_API_KEY` blank. The endpoint returns a rule-based offline report built entirely from the platform's own computed data — same structure, no LLM cost, instant.

---

## 🧪 Running Tests

```bash
make test               # all non-model tests (~641 passing, ~4 s)
make test-models        # ML model tests (two-pass macOS OpenMP isolation)
make test-integration   # backend integration tests (full lifespan, 28 tests)
make test-e2e           # Playwright E2E (requires: make dev in another terminal)
make test-cov           # full suite with HTML coverage report
```

> **macOS note:** LightGBM and PyTorch share conflicting OpenMP libraries on arm64. `make test-models` handles this with two separate pytest invocations.

### Test results

| Suite | Collected | Passing | Failing |
|-------|-----------|---------|---------|
| Non-model (`make test`) | 644 | 641 | 0 |
| Model (`make test-models`) | 37 | 37 | 0 |
| Integration (`make test-integration`) | 28 | 28 | 0 |
| **Total** | **709** | **706** | **0** |

---

## 🔄 Switching Trading Modes

Edit `.env`:

```bash
# Development — no real data, no live orders, PaperBroker in-memory
TRADING_MODE=dev

# Paper trading — live Alpaca market data, simulated fills, engine auto-starts
TRADING_MODE=paper
ALPACA_API_KEY=your_paper_key
ALPACA_SECRET_KEY=your_paper_secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# Live trading — REAL ORDERS (use with extreme caution)
TRADING_MODE=live
ALPACA_API_KEY=your_live_key
ALPACA_SECRET_KEY=your_live_secret
ALPACA_BASE_URL=https://api.alpaca.markets
```

`BrokerFactory` will **refuse to start** in `live` mode without both Alpaca keys set — intentional safety guard.

In `paper` and `live` modes the `TradingEngine` **auto-starts** on API boot. Use the Strategies page or `POST /api/trading/start|stop` to control it at runtime.

---

## 🔌 API Endpoint Reference

All REST endpoints are served at `http://127.0.0.1:8000`. Full interactive docs at `/docs`.

### Portfolio

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/portfolio` | Current portfolio snapshot (cash, equity, positions) |
| GET | `/api/portfolio/price-history` | OHLCV bars for any ticker (`?ticker=AAPL&interval=1d&limit=365`) |
| GET | `/api/portfolio/metrics` | Sharpe, Sortino, Calmar, CAGR, win rate, drawdown, attribution |

### Signals & Trading

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/signals` | Latest strategy signals (last 200) |
| GET | `/api/trading/status` | Engine state, loop count, last-processed timestamps |
| POST | `/api/trading/start` | Start the trading engine |
| POST | `/api/trading/stop` | Stop the trading engine |
| POST | `/api/trading/order` | Submit a manual paper trade |

### Strategies

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/strategies` | All strategies with enabled state and allocation weights |
| PATCH | `/api/strategies/{id}` | Enable or disable a strategy at runtime |

### Analysis

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/analysis/{ticker}` | Composite technical analysis: 10 indicators, rating, Wall St. consensus |
| GET | `/api/bot/watchlist` | Per-ticker analysis for the full engine universe |

### News

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/news` | Stored articles (`?ticker=AAPL&limit=100`) |
| POST | `/api/news/fetch` | On-demand pull from NewsAPI + GDELT for a ticker |

### Backtesting & Optimisation

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/backtest/run` | Launch a backtest (async, returns `run_id`) |
| GET | `/api/backtest/{run_id}/status` | Poll backtest progress |
| GET | `/api/backtest/{run_id}` | Fetch completed backtest result |
| POST | `/api/backtest/walkforward` | Launch walk-forward OOS validation |
| POST | `/api/optimize/run` | Launch Bayesian HPO (Optuna TPE) |
| GET | `/api/optimize/{run_id}/status` | Poll optimisation progress |
| GET | `/api/optimize/{run_id}` | Fetch optimisation result + best params |

### Risk

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/risk/status` | VaR/CVaR, drawdown, halt state, correlation pairs |
| POST | `/api/risk/resume` | Reset the circuit-breaker after a halt |

### AI Analyst

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/ai/analyse` | Generate LLM analyst report from live system data |
| GET | `/api/ai/history` | Session report history (`?limit=10`) |

### WebSocket

| Path | Description |
|------|-------------|
| `/ws/feed` | Real-time event stream: `bar` · `signal` · `fill` · `portfolio_update` · `risk_alert` · `heartbeat` · `backtest_progress` |

---

## 📁 Project Structure

```
algo-trading/
├── packages/
│   ├── quant-engine/                   Python backend
│   │   ├── api/
│   │   │   ├── main.py                 FastAPI app + lifespan startup
│   │   │   ├── deps.py                 AppState, dependency injection
│   │   │   ├── routes/
│   │   │   │   ├── analysis.py         10-indicator technical analysis + yfinance consensus
│   │   │   │   ├── ai_analyst.py       LLM report generation (OpenAI / Anthropic / offline)
│   │   │   │   ├── backtest.py         Backtest + walk-forward async endpoints
│   │   │   │   ├── bot_analysis.py     Engine watchlist: concurrent per-ticker analysis
│   │   │   │   ├── news.py             News GET + on-demand NewsAPI/GDELT pull
│   │   │   │   ├── optimize.py         Bayesian HPO (Optuna TPE) async endpoints
│   │   │   │   ├── portfolio.py        Portfolio snapshot + price-history + metrics
│   │   │   │   ├── risk.py             VaR/CVaR/drawdown status + resume
│   │   │   │   ├── signals.py          Latest strategy signals cache
│   │   │   │   ├── strategies.py       Strategy list + runtime enable/disable
│   │   │   │   └── trading.py          Engine start/stop/status + manual order
│   │   │   └── ws/feed.py              WebSocket broadcast hub (APScheduler heartbeat)
│   │   ├── data/
│   │   │   ├── feeds/
│   │   │   │   ├── alpaca_feed.py      Alpaca REST + WebSocket stream
│   │   │   │   ├── binance_feed.py     Binance REST + WebSocket stream
│   │   │   │   ├── bloomberg_feed.py   B-PIPE adapter (graceful absent startup)
│   │   │   │   ├── coingecko_feed.py   CoinGecko crypto price feed
│   │   │   │   ├── gdelt_feed.py       GDELT news feed (80+ ticker aliases)
│   │   │   │   ├── newsapi_feed.py     NewsAPI.org feed (80+ ticker aliases)
│   │   │   │   ├── sec_edgar_feed.py   SEC EDGAR filings feed
│   │   │   │   ├── yfinance_feed.py    Yahoo Finance fallback
│   │   │   │   └── alpha_vantage_feed.py
│   │   │   ├── pipeline.py             DataPipeline (APScheduler, backfill + streaming)
│   │   │   ├── schemas.py              Bar, NewsArticle, OrderBook Pydantic models
│   │   │   └── store.py                DataStore (SQLAlchemy: SQLite / PostgreSQL)
│   │   ├── features/
│   │   │   └── pipeline.py             40+ indicators · FinBERT sentiment · macro · order-book
│   │   ├── models/
│   │   │   ├── lstm.py                 LSTMForecaster (PyTorch)
│   │   │   ├── transformer.py          TransformerSignalModel (PyTorch)
│   │   │   ├── gaussian_process.py     GP uncertainty quantification (GPyTorch)
│   │   │   ├── gradient_boosting.py    LightGBM regime classifier
│   │   │   ├── reinforcement.py        PPO agent (stable-baselines3)
│   │   │   └── ensemble.py             Meta-learner blending LSTM + Transformer
│   │   ├── strategies/
│   │   │   ├── base.py                 BaseStrategy, Order, TickerState
│   │   │   ├── orchestrator.py         StrategyOrchestrator (weight normalisation)
│   │   │   ├── momentum.py             LSTM+Transformer ensemble; technical fallback
│   │   │   ├── mean_reversion.py       Bollinger Band z-score + ATR stops
│   │   │   ├── stat_arb.py             Engle-Granger cointegration pairs
│   │   │   ├── market_making.py        Avellaneda-Stoikov RL-adjusted quotes
│   │   │   ├── sentiment.py            FinBERT news sentiment z-score
│   │   │   ├── macro_factor.py         VIX × yield curve × earnings
│   │   │   ├── kalman_trend.py         1D Kalman filter innovation
│   │   │   ├── kelly_vol.py            Fractional Kelly + vol targeting
│   │   │   └── vwap_reversion.py       VWAP deviation + ATR/volume confirmation
│   │   ├── backtesting/
│   │   │   ├── engine.py               Event-driven simulation loop (look-ahead safe)
│   │   │   ├── optimizer.py            Bayesian HPO — Optuna TPE, 5 search spaces
│   │   │   ├── portfolio.py            Portfolio accounting: fills, mark-to-market, PnL
│   │   │   ├── runner.py               CLI entrypoint (`python -m backtesting.runner`)
│   │   │   ├── walkforward.py          Expanding-window OOS validation
│   │   │   └── metrics.py              Sharpe · Sortino · Calmar · CAGR · max drawdown
│   │   ├── risk/
│   │   │   ├── manager.py              RiskManager: order gating + limit checks
│   │   │   ├── monitor.py              DrawdownMonitor: circuit-breaker
│   │   │   ├── limits.py               RiskLimits: max drawdown, daily loss, position size
│   │   │   └── README.md               Operational risk reference
│   │   ├── execution/
│   │   │   ├── base.py                 ExecutionBroker ABC
│   │   │   ├── factory.py              BrokerFactory (mode-aware instantiation)
│   │   │   ├── paper_broker.py         PaperBroker (optional partial fills)
│   │   │   ├── alpaca_broker.py        AlpacaBroker (live equity orders)
│   │   │   ├── binance_broker.py       BinanceBroker (live crypto orders)
│   │   │   └── trading_engine.py       Async live loop: bars → features → orders → fills
│   │   ├── config/
│   │   │   ├── settings.py             pydantic-settings (all env vars)
│   │   │   └── strategy_config.yaml    Per-strategy params + 18+ default tickers
│   │   ├── infra/terraform/            11 .tf files — full AWS IaC baseline
│   │   └── docs/concepts/              9 concept guides with formulas + examples
│   └── dashboard/                      React + TypeScript + Vite + Tailwind CSS v4
│       └── src/
│           ├── App.tsx                 Route map (12 pages)
│           ├── components/
│           │   ├── NavBar.tsx          Top bar: WS age counter, engine state
│           │   ├── PriceChart.tsx      Apple-style area chart (green/red gradient)
│           │   ├── PortfolioSummary.tsx Equity curve + stat cards
│           │   ├── SignalTable.tsx      Live signals (WS only — no stale seeding)
│           │   └── NewsFeed.tsx        Article list with sentiment badge pills
│           ├── hooks/
│           │   └── useWebSocketFeed.ts Auto-reconnect WS hook, event dispatch
│           ├── lib/
│           │   ├── api.ts              Typed fetch wrappers for all REST endpoints
│           │   └── types.ts            Shared TypeScript types mirroring API schemas
│           ├── pages/                  12 pages (see Dashboard Pages table above)
│           └── store/
│               └── index.ts           Zustand slices: signals · portfolio · risk
│                                                       fills · news · ws · trading
├── .env.example                        All environment variables documented
├── .gitignore
├── Makefile                            make dev / test / backtest / lint / fmt
└── README.md                           ← you are here
```

---

## 🔑 Environment Variables Reference

Copy `.env.example` to `.env`. Never commit `.env` to Git.

| Variable | Required for | Description |
|----------|-------------|-------------|
| `TRADING_MODE` | Always | `dev` / `paper` / `live` |
| `DATABASE_URL` | Always | SQLite (dev) or `postgresql+psycopg2://...` |
| `LLM_PROVIDER` | AI Analyst | `openai` or `anthropic` (default: `openai`) |
| `LLM_API_KEY` | AI Analyst (LLM mode) | OpenAI or Anthropic key; blank = free offline mode |
| `LLM_MODEL` | AI Analyst | Model override (default: `gpt-4o`) |
| `LLM_MAX_TOKENS` | AI Analyst | Max response tokens (default: `1200`) |
| `ALPACA_API_KEY` | Paper/live equities | Alpaca key |
| `ALPACA_SECRET_KEY` | Paper/live equities | Alpaca secret |
| `ALPACA_BASE_URL` | Paper/live | `https://paper-api.alpaca.markets` or live URL |
| `BINANCE_API_KEY` | Live crypto | Binance key |
| `BINANCE_SECRET_KEY` | Live crypto | Binance secret |
| `BINANCE_TESTNET` | Crypto dev | `true` to use Binance testnet |
| `NEWSAPI_KEY` | News sentiment | NewsAPI.org key (free: 100 req/day) |
| `FMP_API_KEY` | Earnings data | Financial Modeling Prep key |
| `ALPHA_VANTAGE_KEY` | Fundamentals | Alpha Vantage key |
| `POLYGON_KEY` | Tick data | Polygon.io key |
| `BLOOMBERG_APP_NAME` | Bloomberg data | B-PIPE app name (optional) |
| `OIDC_ISSUER_URL` | Production auth | OIDC issuer for JWT validation |
| `OIDC_AUDIENCE` | Production auth | Expected JWT audience claim |
| `API_REQUIRED_ROLE` | Production auth | Role required on mutation endpoints (default: `operator`) |
| `VITE_API_BASE_URL` | Dashboard | Backend URL (default: `http://localhost:8000`) |
| `VITE_WS_URL` | Dashboard | WebSocket URL (default: `ws://localhost:8000/ws/feed`) |
| `LOG_LEVEL` | Logging | `DEBUG` / `INFO` / `WARNING` (default: `INFO`) |
| `LOG_JSON` | Logging | `true` for JSON structured logs (production) |

---

## 🛠 Makefile Commands

```bash
make install            # Install all Python + Node dependencies
make dev                # Start API server + dashboard in parallel
make api                # Start FastAPI server only (uvicorn --reload, 127.0.0.1:8000)
make dashboard          # Start Vite dashboard only (localhost:5173)
make test               # Run non-model tests (~641 passing)
make test-models        # Run ML model tests (two-pass macOS OpenMP isolation)
make test-integration   # Run backend integration tests (full lifespan, 28 tests)
make test-e2e           # Run Playwright E2E tests (needs running dev server)
make test-cov           # Run tests with HTML coverage report
make backtest           # Run backtest via CLI (customise with ARGS=...)
make lint               # ruff (Python) + eslint (TypeScript)
make fmt                # ruff format (Python) + prettier (TypeScript)
```

---

## ☁️ Bloomberg Configuration

Bloomberg provides institutional-quality market data and is used as the anchor source when available. All free sources (yfinance, Alpha Vantage, NewsAPI, GDELT) remain active as fallbacks.

```bash
# .env
BLOOMBERG_APP_NAME=your_app_name   # from Bloomberg Desktop or B-PIPE
```

If `BLOOMBERG_APP_NAME` is unset, the system runs entirely on free sources. The Bloomberg Student plan gives access to Bloomberg Terminal data — once activated, set `BLOOMBERG_APP_NAME` and restart.

---

## ☁️ AWS Terraform Deployment

The full AWS infrastructure baseline lives in `packages/quant-engine/infra/terraform/` (11 `.tf` files):

| Resource | Details |
|----------|---------|
| **VPC** | Public + private subnets across 2 AZs, NAT gateway |
| **ECR** | Container registry (scan-on-push, immutable tags) |
| **ECS Fargate** | Non-root (UID 1001), read-only rootfs, drop ALL capabilities |
| **RDS PostgreSQL 16** | Private subnets, encrypted, deletion-protected |
| **S3** | Artifacts bucket (AES-256, versioned, Glacier lifecycle) |
| **Secrets Manager** | All API keys including `LLM_API_KEY` — never in task definition plaintext |
| **IAM** | Least-privilege execution + task roles |
| **CloudWatch** | Log group, 90-day retention |

```bash
cd packages/quant-engine/infra/terraform
cp terraform.tfvars.example terraform.tfvars
terraform init && terraform validate && terraform plan
terraform apply
```

---

## 🛣 Possible Next Steps

### Immediate Enhancements
- **Bloomberg Student plan** — activate `BLOOMBERG_APP_NAME` for institutional-quality data; the adapter is already implemented in `bloomberg_feed.py`
- **Train ML models** — run `python -m models.lstm --tickers AAPL MSFT NVDA --epochs 50` to load trained weights; momentum strategy automatically switches from technical fallback to LSTM+Transformer ensemble
- **Reddit sentiment** — uncomment `REDDIT_CLIENT_ID/SECRET` in `.env` for r/wallstreetbets sentiment signals
- **FMP earnings calendar** — set `FMP_API_KEY` to enable the Macro Factor strategy's earnings surprise signal

### Scalability
- **Model registry** — store trained model weights in S3 and load on engine startup
- **Kafka event bus** — replace in-process strategy signals with Kafka topics for horizontal scaling
- **Redis bar cache** — cache latest OHLCV bars in Redis to remove DB round-trips on each engine tick
- **Multiple paper portfolios** — run several strategy configurations simultaneously via separate engine instances

### Production Hardening
- **Kubernetes / Helm** — convert ECS Fargate config to Helm charts for Kubernetes deployment
- **Distributed tracing** — add OpenTelemetry spans across DataPipeline → Engine → Broker
- **Refresh token rotation** — extend OIDC seam to include refresh token issuance for dashboard sessions
- **Rate limiting** — add Nginx or AWS WAF rate limiting in front of the API Gateway

### Feature Additions
- **Earnings calendar overlay** — show upcoming earnings dates on the price chart
- **Options chain viewer** — pull and display options flow data (requires Polygon.io paid tier)
- **Multi-account support** — RBAC extension to support multiple trader accounts under one platform
- **Mobile PWA** — convert the dashboard to a Progressive Web App for mobile trading alerts
- **Paper competition mode** — multiple virtual portfolios competing against each other with leaderboard

### Security Hardening
- **mTLS between components** — mutual TLS for DataPipeline → DataStore communication
- **HashiCorp Vault** — migrate from `.env` files to Vault for secret rotation in production
- **PCI-DSS review** — if adding payment processing for subscription billing

---

## 🎓 Technologies Used

### Languages
- **Python 3.11** — backend (async/await, dataclasses, type hints throughout)
- **TypeScript 5** — frontend (strict mode, no `any` in API layer)

### Backend Frameworks & Libraries
| Library | Purpose |
|---------|---------|
| **FastAPI 0.139** | REST API + WebSocket hub |
| **SQLAlchemy 2.0** | ORM — PostgreSQL + SQLite |
| **Alembic** | Database schema migrations |
| **Pydantic v2** | Settings, request/response validation |
| **pydantic-settings** | Environment variable loading |
| **APScheduler** | DataPipeline scheduling (backfill + periodic polls) |
| **structlog** | Structured JSON logging |
| **httpx** | Async HTTP client (GDELT, Alpha Vantage) |
| **websockets** | WS server (Alpaca stream, Binance stream) |
| **aiofiles** | Async file I/O |

### Machine Learning & Quant
| Library | Purpose |
|---------|---------|
| **PyTorch 2.12** | LSTM + Transformer signal models |
| **GPyTorch** | Gaussian Process uncertainty quantification |
| **LightGBM** | Regime classification (gradient boosting) |
| **stable-baselines3** | PPO reinforcement learning agent |
| **transformers (HuggingFace)** | FinBERT sentiment model |
| **scikit-learn** | Feature preprocessing, cointegration helpers |
| **SciPy** | VaR/CVaR, statistical tests (Engle-Granger) |
| **NumPy 2.4** | Vectorised indicator computation |
| **Pandas 3.0** | Time-series bar processing |
| **Optuna** | Bayesian hyperparameter optimisation (TPE) |
| **SHAP** | Model explainability |
| **Gymnasium** | RL environment interface |

### Data Sources
| Source | Data |
|--------|------|
| **Alpaca** | US equity bars + real-time stream |
| **Binance** | Crypto bars + order book + stream |
| **Bloomberg B-PIPE** | Institutional quality (optional) |
| **yfinance** | Free fallback bars for any Yahoo symbol |
| **Alpha Vantage** | Fundamentals, earnings |
| **NewsAPI.org** | News headlines (free: 100 req/day) |
| **GDELT** | Global news events (free) |
| **CoinGecko** | Crypto metadata + price history |
| **SEC EDGAR** | Filings + press releases |

### Frontend
| Library | Purpose |
|---------|---------|
| **React 18** | Component framework |
| **TypeScript 5** | Type safety |
| **Vite 6** | Build tool + dev server |
| **Tailwind CSS v4** | Utility-first styling |
| **Recharts** | OHLCV charts, equity curves, area charts |
| **TanStack Query v5** | REST data fetching, caching, background refresh |
| **Zustand** | Global state (signals, portfolio, risk, fills, news, WS) |
| **React Router v6** | Client-side routing (12 pages) |
| **date-fns** | Date formatting |

### Infrastructure & DevOps
| Tool | Purpose |
|------|---------|
| **Terraform** | AWS IaC (11 `.tf` files) |
| **AWS ECS Fargate** | Container runtime (non-root, read-only rootfs) |
| **AWS RDS PG16** | Production database |
| **AWS ECR** | Container registry |
| **AWS S3** | Model + report storage |
| **AWS Secrets Manager** | API key management |
| **AWS CloudWatch** | Logs (90-day retention) |

### Security
| Mechanism | Implementation |
|-----------|---------------|
| **OIDC / JWT** | `require_operator` FastAPI dependency — validates RS256 JWT against JWKS endpoint |
| **RBAC** | Role claim check (`operator` role required on all mutation endpoints) |
| **TLS 1.2+** | All external API calls use HTTPS; enforced via `httpx` defaults |
| **No secrets in code** | All keys loaded from env vars; `.gitignore` covers `.env` |
| **Localhost binding** | API server binds to `127.0.0.1` — never `0.0.0.0` |
| **Non-root containers** | ECS Fargate task runs as UID 1001 |
| **Parameterised queries** | SQLAlchemy ORM — no raw SQL string formatting |

### Architectural Patterns
- **Event-driven pipeline** — DataPipeline → DataStore → FeaturePipeline → Strategies → RiskManager
- **Strategy pattern** — all 9 strategies inherit `BaseStrategy`; swappable at runtime
- **Dependency injection** — FastAPI `Depends()` + `AppState` dataclass; no module-level globals
- **Circuit-breaker** — `DrawdownMonitor` halts trading on configurable equity loss thresholds
- **Repository pattern** — `DataStore` abstracts all SQL; routes never touch the DB directly
- **Async-first** — trading loop, DataPipeline, and all API routes are fully async

### Testing
| Tool | Purpose |
|------|---------|
| **pytest 9** | Test runner |
| **pytest-asyncio** | Async test support |
| **pytest-cov** | Coverage measurement |
| **Playwright** | E2E browser tests |
| **mypy** | Static type checking (0 errors on targeted modules) |
| **ruff** | Python linting + formatting |

---

## 📊 Project Stats

| Metric | Count |
|--------|-------|
| Strategy classes | 9 |
| Data feed adapters | 9 |
| Dashboard pages | 12 |
| REST API endpoints | 35+ |
| WebSocket event types | 7 |
| Technical indicators (feature pipeline) | 40+ |
| Ticker aliases for news attribution | 80+ |
| Test cases passing | 706 / 709 |
| AWS Terraform resources | 11 `.tf` files |
| Lines of code (approx.) | 25,000+ |

---

## 📚 Further Reading

- [`docs/concepts/cointegration.md`](packages/quant-engine/docs/concepts/cointegration.md) — Engle-Granger, Ornstein-Uhlenbeck, pairs trading
- [`docs/concepts/market_making.md`](packages/quant-engine/docs/concepts/market_making.md) — Avellaneda-Stoikov model, inventory risk
- [`docs/concepts/reinforcement_learning.md`](packages/quant-engine/docs/concepts/reinforcement_learning.md) — MDPs, Bellman equation, PPO
- [`docs/concepts/risk_metrics.md`](packages/quant-engine/docs/concepts/risk_metrics.md) — VaR, CVaR, Sharpe, Sortino, Calmar
- [`docs/concepts/sentiment_nlp.md`](packages/quant-engine/docs/concepts/sentiment_nlp.md) — Transformers, FinBERT, sentiment aggregation
- [`docs/concepts/technical_indicators.md`](packages/quant-engine/docs/concepts/technical_indicators.md) — RSI, MACD, ADX, Bollinger, VWAP, ATR
- [`docs/concepts/gaussian_process.md`](packages/quant-engine/docs/concepts/gaussian_process.md) — GP prior/posterior, kernels, uncertainty
- [`docs/concepts/macro_regimes.md`](packages/quant-engine/docs/concepts/macro_regimes.md) — VIX, yield curve, PEAD, regime multipliers
- [`docs/concepts/aws_cloud.md`](packages/quant-engine/docs/concepts/aws_cloud.md) — ECS Fargate, RDS, Secrets Manager, CloudWatch
- [`risk/README.md`](packages/quant-engine/risk/README.md) — Operational risk management reference

---

Built with Python 3.11 · FastAPI · PyTorch · React · TypeScript · Tailwind CSS · AWS
