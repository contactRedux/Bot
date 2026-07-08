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
├── data/          Feeds (yfinance, Alpaca, Binance, NewsAPI, GDELT, AV, EDGAR, Bloomberg), DataStore, DataPipeline
├── features/      Technical, statistical, fundamental, sentiment, macro indicators + FeaturePipeline
├── models/        LSTM, Transformer, GP, LightGBM, PPO RL, ensemble
├── strategies/    Momentum, mean reversion, stat-arb, market-making, sentiment, macro factor
├── backtesting/   Event-driven engine, simulated broker, portfolio, metrics
├── risk/          RiskManager, VaR/CVaR, drawdown monitor, correlation checker
├── execution/     Paper + Alpaca + Binance broker adapters + BrokerFactory
├── api/           FastAPI REST + WebSocket server
│   ├── routes/    backtest, portfolio, risk, signals, strategies
│   ├── ws/        WebSocket feed (/ws/feed)
│   ├── schemas.py Pydantic request/response models
│   └── main.py    App entry point (lifespan, CORS, routers)
└── tests/         Pytest test suite (risk/, execution/, api/, …)
```

## Running tests

```bash
# All non-model tests (risk, execution, api, backtesting, …)
pytest tests/ --ignore=tests/models -v --tb=short

# Just the API tests
pytest tests/api/ -v
```

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check + uptime |
| GET | `/` | API info + doc links |
| POST | `/api/backtest/run` | Trigger a new backtest |
| GET | `/api/backtest/{id}` | Retrieve completed result |
| GET | `/api/backtest/{id}/status` | Poll run progress |
| GET | `/api/backtest/list` | List all cached runs |
| GET | `/api/portfolio` | Live portfolio state |
| GET | `/api/portfolio/history` | Equity curve history |
| GET | `/api/portfolio/trades` | Recent fills |
| GET | `/api/risk/status` | Risk snapshot (VaR, drawdown, halt) |
| POST | `/api/risk/resume` | Clear a trading halt |
| GET | `/api/risk/var` | Latest VaR/CVaR |
| GET | `/api/risk/limits` | Current RiskLimits config |
| GET | `/api/risk/audit` | Recent non-APPROVE decisions |
| GET | `/api/signals` | Latest signals from all strategies |
| GET | `/api/strategies` | List all strategies |
| PATCH | `/api/strategies/{id}` | Enable/disable at runtime |
| WS | `/ws/feed` | Real-time event stream |

## Trading modes

Set `TRADING_MODE` in the root `.env` file:
- `dev`   — no live connections; uses cached/mock data
- `paper` — live data feeds + simulated order execution
- `live`  — live data feeds + real order execution (requires API keys)
