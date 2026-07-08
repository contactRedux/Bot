# Bot

This repository contains the `algo-trading` monorepo, a full-stack algorithmic AI trading platform for US equities and crypto. The platform combines market data ingestion, feature engineering, machine learning, strategy orchestration, risk controls, backtesting, paper/live execution, and a real-time dashboard in one research-to-execution stack.

## Purpose

The purpose of this project is to provide a single platform where quant research, model development, strategy evaluation, risk control, and operator visibility all live in the same system. Instead of splitting research notebooks, broker scripts, dashboards, and risk checks across disconnected tools, this repository is designed to evolve into a high-grade algorithmic AI trading bot with a clear path from development to paper trading and eventually controlled live deployment.

## Problems This Project Solves

- Reduces fragmentation between research, execution, and monitoring workflows
- Standardizes market data ingestion across multiple providers and asset classes
- Converts raw market, news, and macro inputs into a reusable feature pipeline
- Makes strategy comparison easier through a shared backtesting and walk-forward framework
- Applies centralized risk controls before orders reach execution
- Supports safe progression from development to paper trading to live trading
- Gives operators a dashboard for signals, risk, portfolio state, and execution visibility
- Creates a path for institutional upgrades such as Bloomberg integration, cloud deployment, stronger auth, and reproducible infrastructure

## Current Suite

The main implementation lives in [`algo-trading`](algo-trading).

### Backend capabilities
- Multi-source data layer with feeds for Alpaca, Binance, CoinGecko, yfinance, NewsAPI, GDELT, Alpha Vantage, and SEC EDGAR
- Bloomberg wiring documented and environment-ready, with full B-PIPE implementation planned next
- Feature pipeline with 40+ technical indicators plus sentiment, macro, and statistical features
- ML stack including LSTM, Transformer, Gaussian Process, LightGBM, PPO RL, and ensemble models
- Six implemented strategy families:
  - momentum
  - mean reversion
  - statistical arbitrage
  - market making
  - sentiment/news
  - macro factor
- Event-driven backtester with metrics, reporting, and walk-forward model validation
- Risk engine covering VaR, CVaR, drawdown monitoring, daily loss controls, and correlation-aware scaling
- Execution layer for paper trading plus Alpaca and Binance brokerage adapters
- FastAPI REST API and WebSocket feed for dashboard/operator workflows
- Structured configuration via pydantic settings and strategy YAML

### Frontend capabilities
- React + TypeScript dashboard with overview, backtest, live monitor, news, and risk views
- Zustand stores for signals, portfolio, fills, risk state, and WebSocket status
- Auto-reconnect WebSocket hook for real-time updates
- Typed API client and charting-based operator views

### Quality and validation
- Automated backend tests across API, backtesting, execution, features, models, risk, and strategies
- Frontend lint/build validation in place
- The platform has already been validated with passing backend test suites and successful frontend lint/build checks in its current state

## What Will Be Added Next

The roadmap for moving this repository toward a more institutional-grade algorithmic AI trading platform includes:

- Security/runtime upgrades including localhost-safe defaults, authentication, RBAC, secret management, and auditability
- Bloomberg B-PIPE full implementation
- AWS Terraform-based infrastructure baseline
- Price chart REST OHLC wiring for the dashboard
- Integration and E2E test coverage
- Incremental hardening toward `mypy --strict`
- Execution realism improvements such as partial fills, queue awareness, and initial microstructure features

## Repository layout

- [`algo-trading`](algo-trading) — main trading platform monorepo
- [`algo-trading/README.md`](algo-trading/README.md) — detailed product and developer guide
- [`algo-trading/next-steps.md`](algo-trading/next-steps.md) — phased roadmap and execution plan

## Notes

This project is intended to grow into a high-grade algorithmic AI trading bot, but it should do so safely and honestly. Near-term upgrades focus first on security, control, reliability, and reproducibility before pushing further toward institutional-grade execution and market microstructure sophistication.