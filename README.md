# Bot

This repository contains the `algo-trading` monorepo, a full-stack algorithmic AI trading platform for US equities and crypto. The platform combines market data ingestion, feature engineering, machine learning, strategy orchestration, risk controls, backtesting, paper/live execution, and a real-time dashboard in one research-to-execution stack.

## Purpose

The purpose of this project is to provide a single platform where quant research, model development, strategy evaluation, risk control, and operator visibility all live in the same system. Instead of splitting research notebooks, broker scripts, dashboards, and risk checks across disconnected tools, this repository evolves into a high-grade algorithmic AI trading bot with a clear path from development to paper trading and eventually controlled live deployment.

## Problems This Project Solves

- Reduces fragmentation between research, execution, and monitoring workflows
- Standardizes market data ingestion across multiple providers and asset classes
- Converts raw market, news, and macro inputs into a reusable feature pipeline
- Makes strategy comparison easier through a shared backtesting and walk-forward framework
- Applies centralized risk controls before orders reach execution
- Supports safe progression from development to paper trading to live trading
- Gives operators a dashboard for signals, risk, portfolio state, and execution visibility

## Current State

All 7 institutional-grade upgrade phases are **complete**. The platform is validated end-to-end with 706 passing tests, 0 failures.

| Suite | Passing | Failing |
|---|---|---|
| `make test` — unit (644 collected) | 641 | 0 |
| `make test-models` — ML suite (37 collected) | 37 | 0 |
| `make test-integration` — integration (28 collected) | 28 | 0 |
| **Total** | **706** | **0** |

Static analysis: `mypy` 0 errors on 16 source files · `ruff` clean on all touched files · ESLint + TypeScript build clean.

## What Was Built

### Original platform (sub-tasks 1–11)

| Sub-task | What it built |
|---|---|
| 1 | Monorepo scaffold, `.env.example`, `pyproject.toml`, `Makefile`, Vite/React scaffold |
| 2 | 9 data feed adapters (Alpaca, Binance, CoinGecko, yfinance, NewsAPI, GDELT, Alpha Vantage, SEC EDGAR, Bloomberg), `DataStore` (SQLAlchemy), `DataPipeline` |
| 3 | `FeaturePipeline` — 40+ technical indicators, statistical, sentiment, macro, and order-book imbalance features |
| 4 | LSTM, Transformer, Gaussian Process, LightGBM, PPO RL, and Ensemble models + `ModelRegistry` + walk-forward CV |
| 5 | 6 strategy families (momentum, mean reversion, stat-arb, market-making, sentiment/news, macro factor) + `StrategyOrchestrator` |
| 6 | Event-driven `BacktestEngine` + `SimulatedBroker` + `BacktestReport` + CLI runner |
| 7 | `RiskManager` (6-stage gate) + VaR/CVaR + `DrawdownMonitor` + correlation-aware scaling |
| 8 | `PaperBroker` + `AlpacaBroker` + `BinanceBroker` + `BrokerFactory` |
| 9 | FastAPI REST + WebSocket server, all route modules |
| 10 | React dashboard (Overview, Backtest, Live, News, Risk pages) + Zustand stores + WS hook |
| 11 | 9 concept docs in `docs/concepts/` |

### Institutional-grade upgrade phases (1–7)

| Phase | What it built |
|---|---|
| 1 — Security | `localhost` binding, `require_operator` OIDC bearer seam, RBAC on 4 mutation endpoints, structured AUDIT log lines |
| 2 — Bloomberg | `bloomberg_feed.py` (optional `blpapi`), Bloomberg-first pipeline priority, sentiment quality weights |
| 3 — AWS IaC | 11 Terraform files: VPC, ECR, ECS Fargate, RDS PostgreSQL 16, S3, Secrets Manager, IAM, CloudWatch |
| 4 — PriceChart | `GET /api/portfolio/price-history`, `DataStore` wired into `AppState`, `Overview.tsx` live OHLC data via `useQuery` |
| 5 — Integration/E2E | 28 integration tests (lifespan, health, WebSocket heartbeat, price-history seeded flow, backtest round-trip) + Playwright E2E scaffold |
| 6 — mypy hardening | Per-module `[[tool.mypy.overrides]]`; 0 errors on 16 source files; fixed 3 latent production bugs silently swallowed by `except Exception` |
| 7 — Execution realism | Sqrt-impact slippage model, partial fills in backtester + paper broker, order-book imbalance feature, 46 new tests |

## Backend Capabilities

- Multi-source data layer: Alpaca, Binance, CoinGecko, yfinance, NewsAPI, GDELT, Alpha Vantage, SEC EDGAR, Bloomberg (optional `blpapi`)
- Feature pipeline: 40+ technical indicators + sentiment + macro + order-book imbalance
- ML stack: LSTM, Transformer, Gaussian Process, LightGBM, PPO RL, and ensemble models
- Six strategy families with shared `StrategyOrchestrator`
- Event-driven backtester with sqrt-impact slippage and partial fill simulation
- Risk engine: VaR, CVaR, drawdown monitoring, daily loss controls, correlation-aware scaling
- Execution layer: paper broker (partial fill mode), Alpaca and Binance adapters
- FastAPI REST API + WebSocket feed
- OIDC authentication seam with RBAC on control-plane mutations
- AWS Terraform IaC baseline (11 files) in `infra/terraform/`

## Frontend Capabilities

- React + TypeScript dashboard: Overview, Backtest, Live, News, Risk pages
- Live OHLC price chart wired to `GET /api/portfolio/price-history` (60s refresh)
- Zustand stores for signals, portfolio, fills, risk state, and WebSocket status
- Auto-reconnect WebSocket hook for real-time updates
- Typed API client

## Makefile Targets

| Target | What it runs |
|---|---|
| `make dev` | API on :8000 + dashboard on :5173 |
| `make test` | Unit test suite (non-model) |
| `make test-models` | ML model suite (macOS OpenMP-isolated) |
| `make test-integration` | Integration test suite |
| `make test-e2e` | Playwright E2E (requires running dev server) |
| `make test-cov` | Coverage report → `htmlcov/index.html` |
| `make backtest` | CLI backtest runner |
| `make lint` | ruff + ESLint |

## Repository Layout

- [`algo-trading/`](algo-trading) — main trading platform monorepo
- [`algo-trading/README.md`](algo-trading/README.md) — detailed developer guide, API reference, architecture diagram
- [`algo-trading/next-steps.md`](algo-trading/next-steps.md) — full phased roadmap with per-phase delivery notes and final validation matrix
- [`algo-trading/algo-trading-plan.md`](algo-trading/algo-trading-plan.md) — product plan and sub-task breakdown
- [`algo-trading/infra/terraform/`](algo-trading/infra/terraform) — AWS IaC (VPC, ECR, ECS, RDS, S3, Secrets Manager)
- [`algo-trading/packages/quant-engine/`](algo-trading/packages/quant-engine) — Python backend (FastAPI, ML, backtesting, execution, risk)
- [`algo-trading/packages/dashboard/`](algo-trading/packages/dashboard) — React/TypeScript frontend

## Notes

This project is intended to grow into a high-grade algorithmic AI trading bot, but it does so safely and honestly. All phases prioritized security, control, reliability, and reproducibility before pushing toward institutional-grade execution and market microstructure sophistication.
