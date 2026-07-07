# quant-engine

Python backend for the algorithmic trading platform. Contains data ingestion, feature engineering, ML models, strategy logic, backtesting, risk management, and the FastAPI server.

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[data,ml,api,dev]"
```

## Structure

```
quant-engine/
├── config/        Settings (pydantic-settings), structured logging
├── data/          Feeds (yfinance, Alpaca, Binance, NewsAPI, GDELT, AV, EDGAR), DataStore, DataPipeline
├── features/      Technical, statistical, fundamental, sentiment, macro indicators + FeaturePipeline
├── models/        LSTM, Transformer, GP, LightGBM, PPO RL, ensemble
├── strategies/    Momentum, mean reversion, stat-arb, market-making, sentiment, macro factor
├── backtesting/   Event-driven engine, simulated broker, portfolio, metrics
├── risk/          RiskManager, VaR/CVaR, drawdown monitor
├── execution/     Paper + Alpaca + Binance broker adapters
├── api/           FastAPI REST + WebSocket endpoints
└── tests/         Pytest test suite
```

## Running tests

```bash
pytest tests/ -v --tb=short
```

## Trading modes

Set `TRADING_MODE` in the root `.env` file:
- `dev`   — no live connections; uses cached/mock data
- `paper` — live data feeds + simulated order execution
- `live`  — live data feeds + real order execution (requires API keys)
