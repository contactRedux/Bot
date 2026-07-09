# Algorithmic Trading Platform

A full-stack algorithmic AI trading platform for US equities and crypto, built as a Python/TypeScript monorepo. The project combines market data ingestion, feature engineering, machine learning, strategy orchestration, risk controls, backtesting, Bayesian parameter optimisation, walk-forward validation, paper/live execution, and a real-time dashboard in one research-to-execution stack.

## Purpose

The purpose of this project is to provide a single platform where quant research, model development, strategy evaluation, risk control, and operator visibility all live in the same system. Instead of splitting research notebooks, broker scripts, dashboards, and risk checks across disconnected tools, this codebase provides one coherent trading platform that can evolve toward a high-grade algorithmic AI trading bot.

## Problems This Project Solves

- Reduces fragmentation between research, execution, and monitoring workflows
- Standardises market data ingestion across multiple providers and asset classes
- Converts raw market, news, and macro inputs into a reusable feature pipeline
- Makes strategy comparison easier through a shared backtesting and walk-forward framework
- Finds optimal strategy parameters using Bayesian search (Optuna TPE) instead of grid search
- Validates strategies on truly unseen data with expanding-window walk-forward OOS testing
- Applies centralised risk controls before orders reach execution
- Supports safe progression from development to paper trading to live trading
- Gives operators a dashboard for signals, risk, portfolio state, and execution visibility
- Creates a path for institutional upgrades such as Bloomberg integration, cloud deployment, stronger auth, and reproducible infrastructure

---

## Current Platform Capabilities

### Backend (`packages/quant-engine`)

- Multi-source data layer with 9 feed adapters: Alpaca, Binance, Bloomberg, CoinGecko, yfinance, NewsAPI, GDELT, Alpha Vantage, and SEC EDGAR
- Bloomberg B-PIPE adapter fully implemented (`data/feeds/bloomberg_feed.py`) with graceful `blpapi`-absent startup; feed priority policy and sentiment quality weights in place
- Feature pipeline with 40+ technical indicators, sentiment, macro, statistical features, and order-book imbalance (live microstructure feature)
- ML stack including LSTM, Transformer, Gaussian Process, LightGBM, PPO RL, and ensemble models
- **Nine implemented strategy families:** momentum, mean reversion, statistical arbitrage (Engle-Granger pairs), market making (Avellaneda-Stoikov), news sentiment, macro factor, **Kalman filter trend**, **Kelly criterion + volatility targeting**, **VWAP mean reversion**
- Event-driven backtester with metrics (Sharpe, Sortino, Calmar, CAGR, max drawdown, profit factor), reporting, partial fill model, square-root market impact slippage, and fee/funding hooks
- **Bayesian hyperparameter optimiser** (`backtesting/optimizer.py`) — Optuna TPE with predefined search spaces for all 5 tuneable strategies; objectives: Sharpe / Sortino / Calmar / total return
- **Walk-forward out-of-sample validation** (`backtesting/walkforward.py`) — expanding-window fold structure; per-fold and aggregate metrics
- Risk engine covering VaR, CVaR, drawdown monitoring, daily loss controls, and correlation-aware scaling
- Execution layer: `PaperBroker` (with optional partial-fill mode), `AlpacaBroker`, `BinanceBroker`; Alpaca stream fixed to run in a thread executor (no event-loop conflict)
- FastAPI REST API and WebSocket feed; localhost-only binding; OIDC Bearer auth seam for production; RBAC on all mutation endpoints
- AWS Terraform IaC baseline (11 `.tf` files: VPC, ECR, ECS Fargate, RDS PG16, S3, Secrets Manager, IAM, CloudWatch)
- Incremental `mypy` hardening — 0 errors on `api/`, `config/settings.py`, `data/store.py`, `execution/base.py`, `strategies/base.py`

### Frontend (`packages/dashboard`)

- React + TypeScript dashboard with pages for Overview, Backtest Explorer, Live Monitor, News Feed, Risk Dashboard, and Strategy Manager
- **Backtest Explorer** has three panels: standard backtest, **Bayesian optimisation** (trial value chart + best-param chips), and **walk-forward validation** (per-fold table + aggregate mean±std grid)
- **Strategy Manager** (`/strategies`) — live engine control (start/stop), trading mode badge, per-strategy enable/disable toggles, loop counter
- Overview page fetches live OHLCV data via TanStack React Query (60 s refresh)
- Zustand stores for signals, portfolio, fills, risk state, news articles, WebSocket status, and trading engine state
- Auto-reconnect WebSocket hook; seeds news and trading status from REST on reconnect
- NavBar shows live engine state indicator (TRADING·PAPER / ENGINE IDLE) and WS connection dot
- Recharts-based visualisations for equity curves, risk metrics, and optimisation trial values
- Typed API client and strict TypeScript configuration; `npx tsc --noEmit` → clean

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
│   │   │   └── routes/       backtest · news · optimize · portfolio · risk · signals
│   │   │                     strategies · trading
│   │   ├── config/           pydantic-settings · strategy YAML · structlog
│   │   ├── infra/terraform/  11 .tf files — full AWS IaC baseline
│   │   └── docs/concepts/    Learning guides for every concept
│   └── dashboard/            React + TypeScript + Vite + Tailwind
│       └── src/
│           ├── components/   PriceChart · PortfolioSummary · SignalTable · RiskPanel …
│           ├── hooks/        useWebSocketFeed (auto-reconnect)
│           ├── lib/          Typed API client + shared types
│           ├── pages/        Overview · Backtest · Live · News · Risk · Strategies
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

Offline / research path:
   BacktestEngine → Optimizer (Optuna TPE) → WalkForwardBacktest → metrics report
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
# Edit .env and add your API keys
```

### 2. Install dependencies

```bash
make install
```

This runs `pip install -e ".[data,ml,api,dev]"` in the quant-engine package and `npm install` in the dashboard.

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

## Running a Backtest

**Via Dashboard:**

Open the **Backtest** page (`/backtest`), fill in tickers, date range, and strategies, then choose one of three modes:

| Panel | What it does |
|-------|-------------|
| **Run Backtest** | Standard single-run simulation with equity curve + trade log |
| **Bayesian Optimisation** | Finds best parameters for a strategy using Optuna TPE (5–200 trials) |
| **Walk-Forward Validation** | Expanding-window OOS test across N folds — the proper overfitting check |

**Via API:**

```bash
# Standard backtest
curl -X POST http://127.0.0.1:8000/api/backtest/run \
  -H "Content-Type: application/json" \
  -d '{"tickers":["AAPL","MSFT"],"strategies":["momentum","kalman_trend"],"start_date":"2022-01-01","end_date":"2025-01-01","initial_capital":100000,"interval":"1d"}'

# Bayesian optimisation (40 trials, maximise Sharpe)
curl -X POST http://127.0.0.1:8000/api/optimize/run \
  -H "Content-Type: application/json" \
  -d '{"strategy":"kalman_trend","tickers":["AAPL","NVDA"],"start_date":"2022-01-01","end_date":"2025-01-01","n_trials":40,"objective":"sharpe"}'

# Walk-forward validation
curl -X POST http://127.0.0.1:8000/api/backtest/walkforward \
  -H "Content-Type: application/json" \
  -d '{"tickers":["AAPL","MSFT","NVDA"],"strategies":["momentum","vwap_reversion"],"start_date":"2020-01-01","end_date":"2025-01-01","n_splits":4,"oos_size_days":252}'
```

**Via CLI:**

```bash
cd packages/quant-engine
python -m backtesting.runner \
  --tickers AAPL MSFT NVDA \
  --strategies momentum mean_reversion kalman_trend \
  --start 2022-01-01 \
  --end 2025-01-01 \
  --capital 100000 \
  --output backtest_results.json
```

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
| **Kalman Trend** | `KalmanTrendStrategy` | 1D Kalman filter; trades normalised innovation `ν/√S` | Any trending |
| **Kelly + Vol Target** | `KellyVolStrategy` | Fractional Kelly sizing; vol targeting scales position by σ_target/σ_realised | Low-vol regimes |
| **VWAP Reversion** | `VWAPReversionStrategy` | VWAP deviation %; ATR volatility filter; volume confirmation | Intraday / institutional |

All strategies are configurable via `config/strategy_config.yaml` and toggleable at runtime via `PATCH /api/strategies/{id}` or the `/strategies` dashboard page.

---

## Parameter Tuning

The optimizer uses **Optuna TPE** (Tree-structured Parzen Estimator) — a Bayesian sequential model-based method that samples the most promising parameter region at each trial based on previous results. It is 10–20× more sample-efficient than grid search.

```python
from backtesting.optimizer import StrategyOptimizer
from strategies.kalman_trend import KalmanTrendStrategy
from strategies.orchestrator import StrategyOrchestrator

def strategy_factory(trial):
    cfg = StrategyOptimizer.kalman_trend_space(trial)
    cfg["enabled"] = True
    return KalmanTrendStrategy(config=cfg, tickers=["AAPL", "NVDA"])

optimizer = StrategyOptimizer(
    bars=bars_dict,          # dict[str, list[OHLCVBar]]
    strategy_factory=strategy_factory,
    orchestrator_factory=lambda strats: StrategyOrchestrator(strategies=strats, config={}),
    n_trials=50,
    objective="sharpe",      # sharpe | sortino | calmar | total_return
)
result = optimizer.run()
print(result.best_params)   # {'observation_noise': 0.82, 'process_noise': 0.03, ...}
print(result.best_value)    # 1.743
```

**Pre-defined search spaces:** `momentum_space`, `mean_reversion_space`, `kelly_vol_space`, `kalman_trend_space`, `vwap_reversion_space`.

**Walk-forward usage:**

```python
from backtesting.walkforward import WalkForwardBacktest

wfb = WalkForwardBacktest(
    bars=bars_dict,
    orchestrator_factory=make_orchestrator,   # called fresh each fold
    n_splits=4,
    oos_size_days=252,   # 1 trading year per fold
    min_train_days=365,
)
results = wfb.run()
print(results.aggregate_metrics())
# {'sharpe_ratio': {'mean': 0.92, 'std': 0.34, 'min': 0.45, 'max': 1.37}, ...}
```

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
- **Secrets Manager** for all API keys — never in task definition plaintext
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
