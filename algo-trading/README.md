# Algorithmic Trading Platform

A full-stack algorithmic AI trading platform for US equities and crypto, built as a Python/TypeScript monorepo. The project combines market data ingestion, feature engineering, machine learning, strategy orchestration, risk controls, backtesting, Bayesian parameter optimisation, walk-forward validation, paper/live execution, an LLM-powered trading analyst, and a real-time dashboard in one research-to-execution stack.

## Purpose

The purpose of this project is to provide a single platform where quant research, model development, strategy evaluation, risk control, operator visibility, and AI-assisted commentary all live in the same system. Instead of splitting research notebooks, broker scripts, dashboards, and risk checks across disconnected tools, this codebase provides one coherent trading platform that can evolve toward a high-grade algorithmic AI trading bot.

## Problems This Project Solves

- Reduces fragmentation between research, execution, and monitoring workflows
- Standardises market data ingestion across multiple providers and asset classes
- Converts raw market, news, and macro inputs into a reusable feature pipeline
- Makes strategy comparison easier through a shared backtesting and walk-forward framework
- Finds optimal strategy parameters using Bayesian search (Optuna TPE) instead of grid search
- Validates strategies on truly unseen data with expanding-window walk-forward OOS testing
- Applies centralised risk controls before orders reach execution
- Supports safe progression from development to paper trading to live trading
- **Provides plain-English analyst reports** via an LLM (GPT-4o / Claude) that explain current market conditions, why the bot made its recent trades, and what to watch next
- Gives operators a dashboard for signals, risk, portfolio state, execution visibility, and AI commentary
- Creates a path for institutional upgrades such as Bloomberg integration, cloud deployment, stronger auth, and reproducible infrastructure

---

## Current Platform Capabilities

### Backend (`packages/quant-engine`)

- Multi-source data layer with 9 feed adapters: Alpaca, Binance, Bloomberg, CoinGecko, yfinance, NewsAPI, GDELT, Alpha Vantage, and SEC EDGAR
- Bloomberg B-PIPE adapter fully implemented (`data/feeds/bloomberg_feed.py`) with graceful `blpapi`-absent startup; feed priority policy and sentiment quality weights in place
- Feature pipeline with 40+ technical indicators, sentiment, macro, statistical features, and order-book imbalance (live microstructure feature)
- ML stack including LSTM, Transformer, Gaussian Process, LightGBM, PPO RL, and ensemble models
- **Nine implemented strategy families:** momentum, mean reversion, statistical arbitrage (Engle-Granger pairs), market making (Avellaneda-Stoikov), news sentiment, macro factor, Kalman filter trend, Kelly criterion + volatility targeting, VWAP mean reversion
- Event-driven backtester with metrics (Sharpe, Sortino, Calmar, CAGR, max drawdown, profit factor), reporting, partial fill model, square-root market impact slippage, and fee/funding hooks
- **Bayesian hyperparameter optimiser** (`backtesting/optimizer.py`) — Optuna TPE with predefined search spaces for all 5 tuneable strategies; objectives: Sharpe / Sortino / Calmar / total return
- **Walk-forward out-of-sample validation** (`backtesting/walkforward.py`) — expanding-window fold structure; per-fold and aggregate metrics
- Risk engine covering VaR, CVaR, drawdown monitoring, daily loss controls, and correlation-aware scaling
- Execution layer: `PaperBroker` (with optional partial-fill mode), `AlpacaBroker`, `BinanceBroker`; Alpaca stream fixed to run in a thread executor (no event-loop conflict)
- **AI Analyst** (`api/routes/ai_analyst.py`) — gathers live portfolio, trade, technical, risk, and news data; sends it to OpenAI (GPT-4o) or Anthropic (Claude) and returns a structured analyst report: executive summary, market commentary, trade rationale, risk assessment, and outlook. Works in offline mode (no API key) via rule-based commentary from the same data
- FastAPI REST API and WebSocket feed; localhost-only binding; OIDC Bearer auth seam for production; RBAC on all mutation endpoints
- AWS Terraform IaC baseline (11 `.tf` files: VPC, ECR, ECS Fargate, RDS PG16, S3, Secrets Manager, IAM, CloudWatch)
- Incremental `mypy` hardening — 0 errors on `api/`, `config/settings.py`, `data/store.py`, `execution/base.py`, `strategies/base.py`

### Frontend (`packages/dashboard`)

- React + TypeScript dashboard with **11 pages**: Overview, Charts, Watchlist, Analysis, **AI Analyst**, Metrics, Backtest Explorer, Live Monitor, News Feed, Risk Dashboard, and Strategy Manager
- **Charts (`/chart`)** — Yahoo-Finance-style OHLCV chart; editable watchlist persisted to localStorage; time ranges 1D/1W/1M/3M/1Y/ALL; yfinance fallback for arbitrary tickers; paper trade panel
- **Watchlist (`/watchlist`)** — persistent per-browser watchlist with live prices, 1-day change, and configurable above/below price alerts stored in localStorage
- **Ticker Analysis (`/analysis`)** — search any ticker for a composite technical rating (Strong Buy → Strong Sell); shows RSI gauge, MACD, Bollinger, moving-average signal scores, indicator grid, and reasoning bullets
- **AI Analyst (`/ai`)** — send live system data (portfolio, trades, technicals, news, risk metrics) to GPT-4o or Claude for a structured plain-English analyst report explaining what is happening in the market and why the bot made each trade. Includes offline fallback (rule-based, free)
- **Portfolio Metrics (`/metrics`)** — live session performance: Sharpe, Sortino, Calmar, max drawdown, win rate, CAGR, profit factor, per-strategy attribution
- **Backtest Explorer** has three panels: standard backtest, **Bayesian optimisation** (trial value chart + best-param chips), and **walk-forward validation** (per-fold table + aggregate mean±std grid)
- **Strategy Manager** (`/strategies`) — live engine control (start/stop), trading mode badge, per-strategy enable/disable toggles, loop counter
- Overview page fetches live OHLCV data via TanStack React Query (60 s refresh)
- Zustand stores for signals, portfolio, fills, risk state, news articles, WebSocket status, and trading engine state
- Auto-reconnect WebSocket hook; seeds news and trading status from REST on reconnect
- NavBar shows live engine state indicator (TRADING·PAPER / ENGINE IDLE) and WS connection dot
- Recharts-based visualisations for equity curves, risk metrics, and optimisation trial values
- Typed API client and strict TypeScript configuration; `npm run build` → clean

### Quality and testing

| Suite | Collected | Passing | Skipped | Failing |
|-------|-----------|---------|---------|---------|
| Non-model (`make test`) | 644 | 641 | 3 | 0 |
| Model (`make test-models`) | 37 | 37 | 0 | 0 |
| Integration (`make test-integration`) | 28 | 28 | 0 | 0 |
| **Total** | **709** | **706** | **3** | **0** |

- `mypy` → **0 errors** on all targeted API + store + execution modules
- `ruff` → **0 errors** on all touched files
- Frontend `npx tsc --noEmit` → **clean**
- Playwright E2E suite scaffolded (`make test-e2e`, requires `make dev` first)

---

## Architecture

```
algo-trading/
├── packages/
│   ├── quant-engine/         Python backend
│   │   ├── data/             9 feed adapters (Alpaca, Binance, Bloomberg, NewsAPI, …)
│   │   ├── features/         40+ indicators + NLP + macro + order-book imbalance
│   │   ├── models/           LSTM · Transformer · GP · LightGBM · PPO RL · Ensemble
│   │   ├── strategies/       9 strategy classes + StrategyOrchestrator
│   │   │   ├── momentum.py           LSTM+Transformer ensemble trend following
│   │   │   ├── mean_reversion.py     Bollinger Band z-score + ATR stops
│   │   │   ├── stat_arb.py           Engle-Granger cointegration pairs trading
│   │   │   ├── market_making.py      Avellaneda-Stoikov RL-adjusted quotes
│   │   │   ├── sentiment.py          FinBERT news sentiment z-score
│   │   │   ├── macro_factor.py       VIX regime + yield-curve + earnings surprise
│   │   │   ├── kalman_trend.py       1D Kalman filter — trades normalised innovation
│   │   │   ├── kelly_vol.py          Fractional Kelly + vol targeting (Moreira & Muir)
│   │   │   └── vwap_reversion.py     VWAP deviation + ATR/volume confirmation
│   │   ├── backtesting/      Event-driven BacktestEngine + partial fills + sqrt slippage
│   │   │   ├── engine.py             Main simulation loop (look-ahead safe)
│   │   │   ├── optimizer.py          Bayesian HPO via Optuna TPE
│   │   │   ├── walkforward.py        Expanding-window OOS validation
│   │   │   └── metrics.py            Sharpe · Sortino · Calmar · CAGR · max drawdown
│   │   ├── risk/             RiskManager · VaR/CVaR · DrawdownMonitor
│   │   ├── execution/        PaperBroker (partial mode) · AlpacaBroker · BinanceBroker
│   │   ├── api/              FastAPI REST + WebSocket /ws/feed
│   │   │   └── routes/       ai_analyst · backtest · news · optimize · portfolio
│   │   │                     risk · signals · strategies · trading · analysis
│   │   ├── config/           pydantic-settings · strategy YAML · structlog
│   │   ├── infra/terraform/  11 .tf files — full AWS IaC baseline
│   │   └── docs/concepts/    Learning guides for every concept
│   └── dashboard/            React + TypeScript + Vite + Tailwind
│       └── src/
│           ├── components/   PriceChart · PortfolioSummary · SignalTable · RiskPanel …
│           ├── hooks/        useWebSocketFeed (auto-reconnect)
│           ├── lib/          Typed API client + shared types
│           ├── pages/        Overview · Charts · Watchlist · Analysis · AiAnalyst
│           │                 Metrics · Backtest · Live · News · Risk · Strategies
│           └── store/        Zustand (signals · portfolio · risk · fills · news · trading)
├── .env.example              All environment variables documented
├── Makefile                  make dev / test / test-integration / test-e2e / backtest / lint
└── README.md                 ← you are here
```

**Data flow:**
```
Bloomberg / Alpaca / Binance / NewsAPI / yfinance
        ↓  data/feeds/  (DataPipeline — APScheduler, backfill + streaming)
   DataStore (SQLite → PostgreSQL on AWS)
        ↓  features/pipeline.py
   Feature matrix (OHLCV + 40+ indicators + sentiment + macro + order-book imbalance)
        ↓  models/  +  strategies/  (9 strategy classes)
   Aggregated orders (StrategyOrchestrator)
        ↓  risk/manager.py
   Risk-gated orders (APPROVE / SCALE_DOWN / REJECT)
        ↓  execution/
   Paper fills (optional partial-fill mode)  OR  Alpaca/Binance live orders
        ↓  api/ws/feed.py
   React Dashboard  (real-time WebSocket)

AI Analyst path (on-demand):
   AppState (portfolio + trades + technicals + signals + news + risk)
        ↓  api/routes/ai_analyst.py
   OpenAI / Anthropic  (or offline rule-based fallback)
        ↓
   Structured analyst report  →  /ai dashboard page
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 20+
- `brew install libomp` (macOS only — required for LightGBM)

### 1. Clone and set up environment

```bash
git clone <repo-url>
cd algo-trading
cp .env.example .env
# Edit .env and add your API keys (only LLM_API_KEY needed for AI Analyst;
# everything else is optional — the platform runs fully offline in dev mode)
```

### 2. Install dependencies

```bash
make install
```

This runs `pip install -e ".[data,ml,api,dev]"` in the quant-engine package and `npm install` in the dashboard.

To also install LLM client libraries for the AI Analyst:

```bash
cd packages/quant-engine
pip install openai          # for GPT-4o
pip install anthropic       # for Claude
# or install both:
pip install -e ".[ai]"
```

### 3. Start everything

```bash
make dev
# → FastAPI server at  http://127.0.0.1:8000  (localhost only)
# → Swagger docs at    http://127.0.0.1:8000/docs
# → Dashboard at       http://localhost:5173
```

> **Security note:** The API binds to `127.0.0.1` (loopback only) by default.
> Never use `--host 0.0.0.0` in development — it exposes unauthenticated
> control-plane endpoints on all network interfaces. In production, place the
> API behind a reverse proxy (AWS ALB/nginx) and set `OIDC_ISSUER_URL` to
> require Bearer token authentication on mutation endpoints.

---

## AI Analyst

The AI Analyst (`/ai` dashboard page, `POST /api/ai/analyse`) sends live system data to an LLM and returns a structured plain-English report. It works with no API key in offline mode.

### What data is sent to the LLM

| Data | Source |
|------|--------|
| Portfolio state (cash, equity, open positions + unrealised PnL) | Live `Portfolio` |
| Recent trades (ticker, side, size, fill price, strategy that fired) | `PaperBroker.fills` |
| Technical analysis per ticker (RSI, MACD, SMA-200, composite rating) | `api/routes/analysis.py` |
| Active strategy signals (what each strategy currently sees) | `AppState.latest_signals` |
| Risk state (drawdown %, halt flag, limits) | `DrawdownMonitor` |
| News headlines from last 48h with sentiment scores | `DataStore.read_news()` |

### Report structure

Each report contains five sections:

| Section | Content |
|---------|---------|
| **Executive Summary** | 1–2 sentence overall state of the system |
| **Market Commentary** | Technical conditions — RSI levels, trend direction, price vs SMA-200 |
| **Trade Rationale** | Why the bot made each recent trade — cites strategy name and signal values |
| **Risk Assessment** | Current drawdown, position concentration, halt state |
| **Outlook** | What conditions would trigger more trades; what to watch |

Plus a **Key Takeaways** bullet list for quick scanning.

### Configuration

```bash
# .env
LLM_PROVIDER=openai               # "openai" or "anthropic"
LLM_API_KEY=sk-...                # your API key
LLM_MODEL=gpt-4o                  # or gpt-4o-mini / claude-3-5-sonnet-20241022
LLM_MAX_TOKENS=1200               # max response tokens
```

**Supported models:**

| Provider | Default | Fast/cheap alternative |
|----------|---------|------------------------|
| OpenAI | `gpt-4o` | `gpt-4o-mini` |
| Anthropic | `claude-3-5-sonnet-20241022` | `claude-3-haiku-20240307` |

**No API key:** Leave `LLM_API_KEY` blank. The endpoint returns an offline report built entirely from the platform's own computed data — same structure, no LLM cost, instant.

### History

`GET /api/ai/history?limit=10` returns the last N reports (in-memory, newest first). The dashboard history panel lets you click any past report to view it again.

---

## Running a Backtest

**Via Dashboard:**

Open the **Backtest** page (`/backtest`), fill in tickers, date range, and strategies, then choose one of three modes:

| Panel | What it does |
|-------|-------------|
| **Run Backtest** | Standard single-run simulation with equity curve + trade log |
| **Bayesian Optimisation** | Finds best parameters for a strategy using Optuna TPE (5–200 trials) |
| **Walk-Forward Validation** | Expanding-window OOS test across N folds — the proper overfitting check |

**Via CLI:**

```bash
cd packages/quant-engine
python -m backtesting.runner \
  --tickers AAPL MSFT NVDA \
  --strategies momentum mean_reversion \
  --start 2022-01-01 \
  --end 2024-12-31 \
  --capital 100000 \
  --output reports/result.json
```

**Example backtest result** (Momentum + Mean Reversion on AAPL/MSFT/NVDA, 2022–2024):

| Metric | Value |
|--------|-------|
| Final equity | $102,620 |
| Total return | +2.62% |
| Max drawdown | -2.75% |
| Total trades | 237 |
| Win rate | 29.5% |
| Profit factor | 1.28× |
| Mean Reversion PnL | +$2,887 |

> 2022–2024 included one of the worst bear markets in decades. Mean Reversion is designed for ranging markets, which is why it outperformed in this period.

---

## Strategy Reference

| Strategy | Class | Signal logic | Best market regime |
|----------|-------|-------------|-------------------|
| Momentum | `MomentumStrategy` | LSTM+Transformer ensemble signal; ADX filter | Trending |
| Mean Reversion | `MeanReversionStrategy` | Bollinger Band z-score; ATR stop | Ranging |
| Stat Arb | `StatArbStrategy` | Engle-Granger cointegration; spread z-score; OU half-life filter | Low-correlation pairs |
| Market Making | `MarketMakingStrategy` | Avellaneda-Stoikov RL-adjusted bid/ask quotes; inventory skew | High-volume, tight spreads |
| Sentiment | `SentimentStrategy` | FinBERT article sentiment z-score; decay weighting | Event-driven |
| Macro Factor | `MacroFactorStrategy` | VIX regime × yield curve × earnings surprise multipliers | Regime-change |
| Kalman Trend | `KalmanTrendStrategy` | 1D Kalman filter; trades normalised innovation `ν/√S` | Any trending |
| Kelly + Vol Target | `KellyVolStrategy` | Fractional Kelly sizing; vol targeting scales position by σ_target/σ_realised | Low-vol regimes |
| VWAP Reversion | `VWAPReversionStrategy` | VWAP deviation %; ATR volatility filter; volume confirmation | Intraday / institutional |

All strategies are configurable via `config/strategy_config.yaml` and toggleable at runtime via `PATCH /api/strategies/{id}` or the `/strategies` dashboard page.

---

## Running Tests

```bash
make test               # all non-model tests (~4s, 641 passing)
make test-models        # ML model tests (macOS OpenMP isolation, two-pass)
make test-integration   # backend integration tests (full lifespan, 28 tests)
make test-e2e           # Playwright E2E (requires: make dev in a second terminal)
make test-cov           # with HTML coverage report
```

Tests are split because LightGBM and PyTorch share conflicting OpenMP libraries on macOS arm64.

---

## Switching Trading Modes

Edit `.env`:

```bash
# Development (no real data, no live orders, PaperBroker)
TRADING_MODE=dev

# Paper trading (real Alpaca market data, simulated fills — auto-starts engine)
TRADING_MODE=paper
ALPACA_API_KEY=your_paper_key
ALPACA_SECRET_KEY=your_paper_secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# Live trading (REAL ORDERS — use with extreme caution)
TRADING_MODE=live
ALPACA_API_KEY=your_live_key
ALPACA_SECRET_KEY=your_live_secret
ALPACA_BASE_URL=https://api.alpaca.markets
```

`BrokerFactory` will **refuse to start** in `live` mode without both API keys set — intentional safety guard.

In `paper` and `live` modes the `TradingEngine` **auto-starts** on API boot. Use the `/strategies` dashboard page or `POST /api/trading/start|stop` to control it at runtime.

---

## Bloomberg Configuration

Bloomberg provides institutional-quality market data and is used as the **anchor** source when available. Free sources (yfinance, Alpha Vantage, NewsAPI, GDELT) remain active as fallbacks.

```bash
# .env
BLOOMBERG_APP_NAME=your_app_name   # from Bloomberg Desktop or B-PIPE
```

If `BLOOMBERG_APP_NAME` is unset the system runs entirely on free sources with no architecture change.

---

## AWS Terraform Deployment

The full AWS infrastructure baseline lives in [`infra/terraform/`](infra/terraform). It provisions:

- **VPC** with public + private subnets across 2 AZs, NAT gateway
- **ECR** repository (scan-on-push, immutable tags)
- **ECS Fargate** — non-root (UID 1001), read-only rootfs, drop ALL capabilities
- **RDS PostgreSQL 16** in private subnets, encrypted, deletion-protected
- **S3** artifacts bucket (AES-256, versioned, Glacier lifecycle)
- **Secrets Manager** for all API keys (including `LLM_API_KEY`) — never in task definition plaintext
- **IAM** least-privilege execution + task roles
- **CloudWatch** log group, 90-day retention

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars  # fill in account_id, db_password, etc.
terraform init && terraform validate && terraform plan
terraform apply
```

---

## Environment Variables Reference

All variables are documented in [`.env.example`](.env.example). Key variables:

| Variable | Required for | Description |
|----------|-------------|-------------|
| `TRADING_MODE` | Always | `dev` / `paper` / `live` |
| `DATABASE_URL` | Always | SQLite (dev) or PostgreSQL (prod) |
| `LLM_PROVIDER` | AI Analyst | `openai` or `anthropic` (default: `openai`) |
| `LLM_API_KEY` | AI Analyst (LLM mode) | OpenAI or Anthropic API key; blank = offline mode |
| `LLM_MODEL` | AI Analyst | Model name override (default: `gpt-4o` / `claude-3-5-sonnet-20241022`) |
| `LLM_MAX_TOKENS` | AI Analyst | Max response tokens (default: `1200`) |
| `ALPACA_API_KEY` | Paper/live equities | Alpaca key |
| `ALPACA_SECRET_KEY` | Paper/live equities | Alpaca secret |
| `ALPACA_BASE_URL` | Paper/live | `https://paper-api.alpaca.markets` or live URL |
| `BINANCE_API_KEY` | Live crypto | Binance key |
| `BINANCE_SECRET_KEY` | Live crypto | Binance secret |
| `NEWSAPI_KEY` | News sentiment | NewsAPI key |
| `FMP_API_KEY` | Earnings / press releases | Financial Modeling Prep key |
| `ALPHA_VANTAGE_KEY` | Fundamentals | Alpha Vantage key |
| `POLYGON_KEY` | Tick data | Polygon.io key |
| `BLOOMBERG_APP_NAME` | Bloomberg data | B-PIPE app name (optional) |
| `AWS_REGION` | Cloud deployment | AWS region |
| `AWS_ACCOUNT_ID` | Cloud deployment | AWS account |
| `S3_BUCKET_NAME` | Model/report storage | S3 bucket |
| `OIDC_ISSUER_URL` | Production auth | OIDC issuer URL for JWT validation |
| `OIDC_AUDIENCE` | Production auth | Expected JWT audience claim |
| `API_REQUIRED_ROLE` | Production auth | Role required for mutation endpoints (default: `operator`) |
| `VITE_API_BASE_URL` | Dashboard | Backend API URL (default: `http://localhost:8000`) |
| `VITE_WS_URL` | Dashboard | WebSocket feed URL |

---

## Makefile Commands

```bash
make install            # install all Python + Node dependencies
make dev                # start API server + dashboard in parallel
make api                # start FastAPI server only (uvicorn --reload, 127.0.0.1:8000)
make dashboard          # start Vite dashboard only
make test               # run non-model tests (641 passing)
make test-models        # run ML model tests (two-pass macOS OpenMP isolation)
make test-integration   # run backend integration tests (full lifespan)
make test-e2e           # run Playwright E2E tests (needs running dev server)
make test-cov           # run tests with HTML coverage report
make backtest           # run example backtest via CLI runner
make lint               # ruff (Python) + eslint (TypeScript)
make fmt                # ruff format (Python) + prettier (TypeScript)
```

---

## Concept Documentation

All theoretical concepts are documented with formulas, code examples, and cross-links:

| Document | Topics |
|----------|--------|
| [`docs/concepts/cointegration.md`](packages/quant-engine/docs/concepts/cointegration.md) | Cointegration, Engle-Granger, Ornstein-Uhlenbeck, pairs trading |
| [`docs/concepts/market_making.md`](packages/quant-engine/docs/concepts/market_making.md) | Bid-ask spread, inventory risk, Avellaneda-Stoikov model |
| [`docs/concepts/reinforcement_learning.md`](packages/quant-engine/docs/concepts/reinforcement_learning.md) | MDPs, Bellman equation, PPO, reward shaping |
| [`docs/concepts/risk_metrics.md`](packages/quant-engine/docs/concepts/risk_metrics.md) | VaR, CVaR, Sharpe, Sortino, Calmar, max drawdown |
| [`docs/concepts/sentiment_nlp.md`](packages/quant-engine/docs/concepts/sentiment_nlp.md) | Transformers, BERT, FinBERT, sentiment aggregation |
| [`docs/concepts/technical_indicators.md`](packages/quant-engine/docs/concepts/technical_indicators.md) | RSI, MACD, ADX, Bollinger Bands, VWAP, ATR |
| [`docs/concepts/gaussian_process.md`](packages/quant-engine/docs/concepts/gaussian_process.md) | GP prior/posterior, kernels, uncertainty quantification |
| [`docs/concepts/macro_regimes.md`](packages/quant-engine/docs/concepts/macro_regimes.md) | VIX, yield curve, USD momentum, PEAD, regime multipliers |
| [`docs/concepts/aws_cloud.md`](packages/quant-engine/docs/concepts/aws_cloud.md) | S3, RDS, ECS Fargate, Secrets Manager, CloudWatch, Terraform IaC |
| [`risk/README.md`](packages/quant-engine/risk/README.md) | Operational risk management reference |
