# Algorithmic Trading Platform

A full-stack algorithmic trading platform covering US equities and crypto, built as a Python/TypeScript monorepo. Implements six strategy families — momentum, mean reversion, statistical arbitrage, market making, news sentiment, and macro factor — plus backtesting, risk management, a FastAPI server, and a React terminal dashboard.

---

## Architecture

```
algo-trading/
├── packages/
│   ├── quant-engine/         Python backend
│   │   ├── data/             8 feed adapters (Alpaca, Binance, Bloomberg, NewsAPI, …)
│   │   ├── features/         40 technical indicators + NLP + macro signals
│   │   ├── models/           LSTM · Transformer · GP · LightGBM · PPO RL · Ensemble
│   │   ├── strategies/       6 strategy classes + StrategyOrchestrator
│   │   ├── backtesting/      Event-driven BacktestEngine + walk-forward CV
│   │   ├── risk/             RiskManager · VaR/CVaR · DrawdownMonitor
│   │   ├── execution/        PaperBroker · AlpacaBroker · BinanceBroker
│   │   ├── api/              FastAPI REST + WebSocket /ws/feed
│   │   ├── config/           pydantic-settings · strategy YAML · structlog
│   │   └── docs/concepts/    Learning guides for every concept
│   └── dashboard/            React + TypeScript + Vite + Tailwind
│       └── src/
│           ├── components/   PriceChart · PortfolioSummary · SignalTable · RiskPanel …
│           ├── hooks/        useWebSocketFeed (auto-reconnect)
│           ├── lib/          Typed API client + shared types
│           ├── pages/        Overview · Backtest · Live · News · Risk
│           └── store/        Zustand slices (signals, portfolio, risk, fills)
├── .env.example              All environment variables documented
├── Makefile                  make dev / test / backtest / lint
└── README.md                 ← you are here
```

**Data flow:**
```
Bloomberg / Alpaca / Binance / NewsAPI / yfinance
        ↓  data/feeds/
   DataStore (SQLite → PostgreSQL on AWS)
        ↓  features/pipeline.py
   Feature matrix (OHLCV + 40 indicators + sentiment + macro)
        ↓  models/  +  strategies/
   Aggregated orders (StrategyOrchestrator)
        ↓  risk/manager.py
   Risk-gated orders (APPROVE / SCALE_DOWN / REJECT)
        ↓  execution/
   Paper fills  OR  Alpaca/Binance live orders
        ↓  api/ws/feed.py
   React Dashboard  (real-time WebSocket)
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

This runs `pip install -e ".[data,ml,api,dev]"` in the quant-engine venv and `npm install` in the dashboard.

### 3. Start the backend API server

```bash
make api
# → FastAPI server at http://localhost:8000
# → Swagger docs at http://localhost:8000/docs
```

### 4. Start the dashboard

```bash
make dev
# → React dev server at http://localhost:5173
```

---

## Running a Backtest

**Via CLI:**

```bash
cd packages/quant-engine
source .venv/bin/activate
python -m backtesting.runner \
  --tickers AAPL MSFT \
  --strategies momentum mean_reversion \
  --start 2022-01-01 \
  --end 2024-01-01 \
  --capital 100000 \
  --output backtest_results.json
```

**Via Dashboard:**

Open the **Backtest** page, select tickers and strategies, set date range and capital, click **Run Backtest**.

**Via Makefile:**

```bash
make backtest
```

---

## Running Tests

```bash
make test          # all non-model tests (fast, ~30s)
make test-models   # model tests (slower, requires libomp on macOS)
```

Tests are split because LightGBM and PyTorch share conflicting OpenMP libraries on macOS arm64.

---

## Switching Trading Modes

Edit `.env`:

```bash
# Development (no real data, no live orders)
TRADING_MODE=dev

# Paper trading (real data, simulated orders)
TRADING_MODE=paper
ALPACA_API_KEY=your_paper_key
ALPACA_SECRET_KEY=your_paper_secret

# Live trading (REAL ORDERS — use with extreme caution)
TRADING_MODE=live
ALPACA_API_KEY=your_live_key
ALPACA_SECRET_KEY=your_live_secret
```

`BrokerFactory` will **refuse to start** in live mode without both API keys set — this is an intentional safety guard.

---

## Bloomberg Configuration

Bloomberg provides institutional-quality market data and is used as the **anchor** source. Free sources (yfinance, Alpha Vantage, NewsAPI) remain fully active as fallbacks.

```bash
# .env
BLOOMBERG_APP_NAME=your_app_name   # from Bloomberg Desktop or B-PIPE
```

If `BLOOMBERG_APP_NAME` is unset, the system runs entirely on free sources with no architecture change. See [`docs/concepts/sentiment_nlp.md`](packages/quant-engine/docs/concepts/sentiment_nlp.md) for how Bloomberg news is weighted in sentiment aggregation.

The Bloomberg feed adapter (`data/feeds/bloomberg_feed.py`) is scaffolded and documented. A live B-PIPE server connection is required for production use.

---

## AWS Configuration

```bash
# .env
AWS_REGION=us-east-1
AWS_ACCOUNT_ID=123456789012
S3_BUCKET_NAME=my-algo-trading-bucket
```

When these are set, the model registry uses S3 for artifact storage and backtest reports are persisted to S3. The database URL is overridden with RDS when deploying to ECS. See [`docs/concepts/aws_cloud.md`](packages/quant-engine/docs/concepts/aws_cloud.md) for the full deployment guide.

---

## Concept Documentation

All theoretical concepts are documented with formulas, code examples, and cross-links to the implementing files:

| Document | Topics |
|----------|--------|
| [`docs/concepts/cointegration.md`](packages/quant-engine/docs/concepts/cointegration.md) | Cointegration, Engle-Granger, Ornstein-Uhlenbeck, pairs trading |
| [`docs/concepts/market_making.md`](packages/quant-engine/docs/concepts/market_making.md) | Bid-ask spread, inventory risk, Avellaneda-Stoikov model |
| [`docs/concepts/reinforcement_learning.md`](packages/quant-engine/docs/concepts/reinforcement_learning.md) | MDPs, Bellman equation, PPO, reward shaping |
| [`docs/concepts/risk_metrics.md`](packages/quant-engine/docs/concepts/risk_metrics.md) | VaR, CVaR, Sharpe, Sortino, Calmar, max drawdown, Ulcer Index |
| [`docs/concepts/sentiment_nlp.md`](packages/quant-engine/docs/concepts/sentiment_nlp.md) | Transformers, BERT, FinBERT, sentiment aggregation |
| [`docs/concepts/technical_indicators.md`](packages/quant-engine/docs/concepts/technical_indicators.md) | RSI, MACD, ADX, Bollinger Bands, VWAP, Ichimoku, ATR |
| [`docs/concepts/gaussian_process.md`](packages/quant-engine/docs/concepts/gaussian_process.md) | GP prior/posterior, kernels, uncertainty quantification |
| [`docs/concepts/macro_regimes.md`](packages/quant-engine/docs/concepts/macro_regimes.md) | VIX, yield curve, USD momentum, PEAD, regime multipliers |
| [`docs/concepts/aws_cloud.md`](packages/quant-engine/docs/concepts/aws_cloud.md) | S3, RDS, ECS Fargate, Secrets Manager, CloudWatch |
| [`risk/README.md`](packages/quant-engine/risk/README.md) | Operational risk management reference |

---

## Environment Variables Reference

All variables are documented in [`.env.example`](.env.example). Key variables:

| Variable | Required for | Description |
|----------|-------------|-------------|
| `TRADING_MODE` | Always | `dev` / `paper` / `live` |
| `DATABASE_URL` | Always | SQLite (dev) or PostgreSQL (prod) |
| `ALPACA_API_KEY` | Paper/live equities | Alpaca key |
| `ALPACA_SECRET_KEY` | Paper/live equities | Alpaca secret |
| `BINANCE_API_KEY` | Live crypto | Binance key |
| `BINANCE_SECRET_KEY` | Live crypto | Binance secret |
| `NEWSAPI_KEY` | News sentiment | NewsAPI key |
| `ALPHA_VANTAGE_KEY` | Fundamentals | Alpha Vantage key |
| `BLOOMBERG_APP_NAME` | Bloomberg data | B-PIPE app name (optional) |
| `AWS_REGION` | Cloud deployment | AWS region |
| `AWS_ACCOUNT_ID` | Cloud deployment | AWS account |
| `S3_BUCKET_NAME` | Model/report storage | S3 bucket |
| `VITE_API_BASE_URL` | Dashboard | Backend API URL |
| `VITE_WS_URL` | Dashboard | WebSocket feed URL |

---

## Makefile Commands

```bash
make install        # install all Python + Node dependencies
make dev            # start dashboard dev server (Vite HMR)
make api            # start FastAPI server (uvicorn --reload)
make test           # run non-model tests
make test-models    # run model tests (separate for macOS OpenMP isolation)
make backtest       # run example backtest via CLI runner
make lint           # ruff + mypy (Python) + eslint (TypeScript)
make fmt            # black + isort (Python) + prettier (TypeScript)
```
