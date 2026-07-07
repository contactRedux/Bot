# Algorithmic Trading Platform

A full-stack, multi-strategy algorithmic trading platform combining **deep learning**, **reinforcement learning**, **NLP-driven sentiment analysis**, and **quantitative finance** in a single monorepo.

Built as both a functional trading system and a learning vehicle — every module is heavily commented with the mathematical and economic intuition behind each decision.

---

## What This System Does

- **Ingests** real-time and historical market data from multiple sources (Alpaca, Binance, yfinance, NewsAPI, GDELT, Alpha Vantage, SEC EDGAR)
- **Engineers features**: 20+ technical indicators, cointegration statistics, FinBERT NLP sentiment, macro regime signals
- **Runs 6 concurrent strategies**: Momentum, Mean Reversion, Statistical Arbitrage, Market Making, News Sentiment, Macro Factor
- **Models signals** with LSTM, Transformer, Gaussian Process, LightGBM, and a PPO Reinforcement Learning agent
- **Backtests** strategies on historical data with realistic slippage, commissions, and per-strategy attribution
- **Manages risk** via VaR, CVaR, drawdown circuit-breaker, and correlation concentration limits
- **Executes** on Alpaca (equities) and Binance (crypto) — paper mode by default, live with a single config change
- **Visualises** everything in a real-time React dashboard with price charts, signal tables, news sentiment feed, and backtest explorer

---

## Monorepo Layout

```
algo-trading/
├── packages/
│   ├── quant-engine/          # Python backend (ML, strategies, API)
│   │   ├── data/              # Data ingestion from all sources
│   │   ├── features/          # Feature engineering (technical, NLP, macro)
│   │   ├── models/            # LSTM, Transformer, GP, LightGBM, RL agent
│   │   ├── strategies/        # 6 strategy implementations + orchestrator
│   │   ├── backtesting/       # Event-driven simulation engine
│   │   ├── risk/              # VaR, CVaR, drawdown monitor, position limits
│   │   ├── execution/         # Paper + Alpaca + Binance broker adapters
│   │   ├── api/               # FastAPI REST + WebSocket server
│   │   ├── config/            # Settings, strategy YAML, logging
│   │   ├── tests/             # Full test suite
│   │   └── docs/              # Concept explainers (math + finance theory)
│   └── dashboard/             # React + TypeScript + Tailwind frontend
│       └── src/
│           ├── components/    # PriceChart, SignalTable, NewsFeed, RiskPanel
│           ├── pages/         # Overview, BacktestExplorer, Live, News, Risk
│           ├── hooks/         # WebSocket feed hook
│           └── store/         # Zustand state slices
├── .env.example               # All environment variables documented here
├── Makefile                   # Developer commands
└── README.md                  # This file
```

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- Node.js 20+
- Git

### 2. Clone and configure

```bash
git clone <repo-url> algo-trading
cd algo-trading

# Copy and fill in your API keys
cp .env.example .env
# Edit .env — at minimum set TRADING_MODE=paper and your Alpaca keys
```

### 3. Install dependencies

```bash
make install
```

### 4. Start the development environment

```bash
make dev
# API server → http://localhost:8000  (OpenAPI docs at /docs)
# Dashboard  → http://localhost:5173
```

---

## Running a Backtest

```bash
# Default: all strategies, AAPL + MSFT, 2023
make backtest

# Custom run
make backtest ARGS="--strategies momentum stat_arb --start 2020-01-01 --end 2024-01-01 --tickers AAPL MSFT NVDA BTC-USD"
```

---

## Switching to Paper / Live Mode

```bash
# In .env:
TRADING_MODE=paper          # Live data + simulated execution (Alpaca paper account)
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

> **Warning:** Setting `TRADING_MODE=live` routes real orders to your brokerage. Only do this after extensive paper trading validation.

---

## Strategies

| Strategy | Signal Source | Paradigm |
|---|---|---|
| Momentum | LSTM + Transformer ensemble | Trend following |
| Mean Reversion | Bollinger Bands + Z-score | Statistical |
| Statistical Arbitrage | Cointegration spread z-score | Market neutral |
| Market Making | PPO RL agent + order book | Liquidity provision |
| News Sentiment | FinBERT aggregated score | Event driven |
| Macro Factor | VIX + yield curve + earnings | Top-down |

---

## Learning Guides

Concept documentation is in `packages/quant-engine/docs/concepts/`:

- [`cointegration.md`](packages/quant-engine/docs/concepts/cointegration.md) — Pairs trading math
- [`market_making.md`](packages/quant-engine/docs/concepts/market_making.md) — Avellaneda-Stoikov model
- [`reinforcement_learning.md`](packages/quant-engine/docs/concepts/reinforcement_learning.md) — PPO for execution
- [`risk_metrics.md`](packages/quant-engine/docs/concepts/risk_metrics.md) — VaR, CVaR, Sharpe, drawdown
- [`sentiment_nlp.md`](packages/quant-engine/docs/concepts/sentiment_nlp.md) — FinBERT sentiment pipeline
- [`technical_indicators.md`](packages/quant-engine/docs/concepts/technical_indicators.md) — Indicator math
- [`gaussian_process.md`](packages/quant-engine/docs/concepts/gaussian_process.md) — GP uncertainty models
- [`macro_regimes.md`](packages/quant-engine/docs/concepts/macro_regimes.md) — VIX, yield curve, earnings

---

## Tech Stack

| Layer | Technology |
|---|---|
| ML / DL | PyTorch — LSTM, Transformer, Gaussian Process |
| NLP | HuggingFace Transformers + FinBERT |
| Reinforcement Learning | stable-baselines3 (PPO) + Gymnasium |
| Tabular ML | LightGBM + SHAP |
| Technical Indicators | pandas-ta |
| API Server | FastAPI + Uvicorn |
| Database | SQLite (dev) / PostgreSQL (prod) via SQLAlchemy |
| Equities Execution | Alpaca |
| Crypto Execution | Binance |
| Frontend | React + TypeScript + Vite |
| Charting | Recharts |
| Styling | Tailwind CSS (dark mode) |
| State | Zustand |

---

## Development Commands

```bash
make install      # Install all Python + Node dependencies
make dev          # Start API + dashboard
make test         # Run Python test suite
make test-cov     # Tests with HTML coverage report
make backtest     # Run backtest CLI
make lint         # Lint Python (ruff) + TypeScript (eslint)
make fmt          # Format Python (ruff) + TypeScript (prettier)
```

---

## Phase 2 (Not Yet Implemented)

- Options chain trading (Black-Scholes pricing, Greeks, Polygon.io options data)
- Automated hyperparameter tuning (Bayesian optimisation)
- HFT / tick-level strategies
