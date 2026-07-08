# Next Steps

This document captures the planned implementation roadmap for evolving the monorepo into a higher-grade algorithmic AI trading platform.

## Delivery order

1. Security/runtime prerequisites
2. Bloomberg B-PIPE implementation
3. AWS IaC baseline
4. PriceChart REST OHLC wiring
5. Integration/E2E suite
6. `mypy --strict` hardening
7. Execution realism + microstructure layer

This order is driven by the project's security requirements and the goal of moving the platform closer to an institutional-grade tool.

---

## Phase 1 — Security/runtime prerequisites

### Goals
- Eliminate non-compliant network binding defaults
- Add API authentication with an OIDC-compatible bearer validation seam
- Add RBAC for sensitive endpoints
- Move secret sourcing to environment/AWS Secrets Manager integration seams
- Add immutable audit logging for control-plane actions
- Make local/dev startup deterministic and documented

### Files to inspect/change
- [`Makefile`](Makefile)
- [`api/main.py`](packages/quant-engine/api/main.py)
- [`api/deps.py`](packages/quant-engine/api/deps.py)
- [`api/routes/backtest.py`](packages/quant-engine/api/routes/backtest.py)
- [`api/routes/risk.py`](packages/quant-engine/api/routes/risk.py)
- [`api/routes/strategies.py`](packages/quant-engine/api/routes/strategies.py)
- [`settings.py`](packages/quant-engine/config/settings.py)
- [`logging.py`](packages/quant-engine/config/logging.py)
- [`.env.example`](.env.example)
- [`README.md`](README.md)

### Planned changes
- Change local API startup docs/defaults from `0.0.0.0` to `127.0.0.1`
- Add auth settings:
  - `OIDC_ISSUER_URL`
  - `OIDC_AUDIENCE`
  - `API_REQUIRED_ROLE`
  - `AWS_SECRETS_PREFIX`
- Add auth dependency layer in [`get_app_state()`](packages/quant-engine/api/deps.py:74) and related providers
- Protect mutation/control endpoints first:
  - risk resume
  - strategy toggle
  - backtest run/delete
- Add structured audit events for:
  - strategy enable/disable
  - risk resume
  - backtest launches/deletes
- Keep implementation minimal: token validation seam + role checks, not a full identity platform build

### Validation
- Existing API tests
- New auth/RBAC unit tests
- Manual local health check with localhost-only bind
- Ruff/mypy only on touched auth files if global lint is still noisy

---

## Phase 2 — Bloomberg B-PIPE full implementation

### Goals
- Add a real Bloomberg adapter implementation
- Support historical bars and news fetch
- Wire it into the feed selection policy
- Preserve fallback to free sources when Bloomberg is unavailable

### Files to inspect/change
- [`__init__.py`](packages/quant-engine/data/feeds/__init__.py)
- [`base.py`](packages/quant-engine/data/feeds/base.py)
- [`pipeline.py`](packages/quant-engine/data/pipeline.py)
- [`store.py`](packages/quant-engine/data/store.py)
- [`settings.py`](packages/quant-engine/config/settings.py)
- [`sentiment.py`](packages/quant-engine/features/sentiment.py)
- [`sentiment_nlp.md`](packages/quant-engine/docs/concepts/sentiment_nlp.md)
- [`pyproject.toml`](packages/quant-engine/pyproject.toml)
- New file: [`bloomberg_feed.py`](packages/quant-engine/data/feeds/bloomberg_feed.py)
- New tests: [`test_bloomberg_feed.py`](packages/quant-engine/tests/data/test_bloomberg_feed.py)

### Planned changes
- Add Bloomberg settings:
  - `BLOOMBERG_HOST`
  - `BLOOMBERG_PORT`
  - `BLOOMBERG_APP_NAME`
  - `BLOOMBERG_TIMEOUT_SECONDS`
- Implement adapter with optional dependency on `blpapi`
- Support:
  - historical OHLCV request mapping
  - news headline/body retrieval where available
- Add provider-priority policy:
  - Bloomberg first for covered assets
  - Alpaca/Binance/yfinance/CoinGecko fallback
- Avoid startup failure if Bloomberg is not installed/configured
- Keep design synchronous for fetches unless the existing code requires Bloomberg streaming

### Validation
- Unit tests with mocked `blpapi`
- Pipeline tests proving graceful fallback
- Smoke test import path with Bloomberg dependency absent

### Phase 2 non-goals
- Not a full Bloomberg Terminal replacement or analytics workstation
- Not a tick-by-tick market data plant or exchange co-location stack
- Not a guaranteed real-time Bloomberg streaming plant unless the surrounding environment and entitlements support it
- Not a removal of free-source fallbacks; Bloomberg augments and prioritizes the stack rather than replacing resilience paths
- Not a full OMS/EMS build with human trader workflows, approvals, or post-trade operations

---

## Phase 3 — AWS IaC baseline

### Goals
- Add reproducible AWS deployment baseline
- Keep secrets out of code
- Support ECS Fargate, ECR, RDS, S3, CloudWatch, Secrets Manager
- Use secure defaults aligned with project rules

### Files to add/change
- New infra tree:
  - [`providers.tf`](infra/terraform/providers.tf)
  - [`variables.tf`](infra/terraform/variables.tf)
  - [`main.tf`](infra/terraform/main.tf)
  - [`network.tf`](infra/terraform/network.tf)
  - [`ecr.tf`](infra/terraform/ecr.tf)
  - [`ecs.tf`](infra/terraform/ecs.tf)
  - [`rds.tf`](infra/terraform/rds.tf)
  - [`s3.tf`](infra/terraform/s3.tf)
  - [`iam.tf`](infra/terraform/iam.tf)
  - [`secrets.tf`](infra/terraform/secrets.tf)
  - [`outputs.tf`](infra/terraform/outputs.tf)
- Docs:
  - [`aws_cloud.md`](packages/quant-engine/docs/concepts/aws_cloud.md)
  - [`README.md`](README.md)

### Planned changes
- Use Terraform, not CDK initially, for a deterministic baseline
- Secure defaults:
  - ECS tasks run as non-root
  - private subnets for services and DB
  - Secrets Manager injection
  - S3 encryption
  - RDS encryption
  - CloudWatch logging
- No `0.0.0.0` app guidance in docs; external exposure should be via AWS LB config, not app binding guidance
- Add environment contract mapping from runtime settings to secret names

### Validation
- `terraform fmt -check`
- `terraform validate`
- Documentation review against actual variable names

---

## Phase 4 — PriceChart REST OHLC wiring

### Goals
- Replace empty chart placeholder data with real REST-backed OHLC/close series
- Keep dashboard types aligned with backend schema
- Add the minimal backend endpoint required

### Files to inspect/change
- [`PriceChart.tsx`](packages/dashboard/src/components/PriceChart.tsx)
- [`Overview.tsx`](packages/dashboard/src/pages/Overview.tsx)
- [`api.ts`](packages/dashboard/src/lib/api.ts)
- [`types.ts`](packages/dashboard/src/lib/types.ts)
- [`portfolio.py`](packages/quant-engine/api/routes/portfolio.py)
- [`schemas.py`](packages/quant-engine/api/schemas.py)
- [`store.py`](packages/quant-engine/data/store.py)
- Tests:
  - [`test_portfolio.py`](packages/quant-engine/tests/api/test_portfolio.py)

### Planned changes
- Add backend endpoint like `/api/portfolio/price-history`
- Fetch bars from [`DataStore.read_bars()`](packages/quant-engine/data/store.py:278)
- Return normalized points for chart consumption
- Add frontend typed API helper
- Update [`Overview.tsx`](packages/dashboard/src/pages/Overview.tsx) to request data for the primary ticker instead of passing `[]`
- Keep the current line/EMA chart unless candlestick support is explicitly requested later

### Validation
- Backend API tests for the new endpoint
- Dashboard lint/build
- Optional manual render sanity check

---

## Phase 5 — Integration/E2E suite

### Goals
- Verify backend and dashboard work together end-to-end
- Cover critical flows: health, portfolio, chart data, backtest, strategy toggle, risk resume

### Files to add/change
- New backend integration tests:
  - [`test_api_app_state.py`](packages/quant-engine/tests/integration/test_api_app_state.py)
  - [`test_portfolio_chart_flow.py`](packages/quant-engine/tests/integration/test_portfolio_chart_flow.py)
- Dashboard E2E if toolchain is added:
  - [`package.json`](packages/dashboard/package.json)
  - [`playwright.config.ts`](packages/dashboard/playwright.config.ts)
  - [`overview.spec.ts`](packages/dashboard/tests/e2e/overview.spec.ts)

### Planned changes
- Add backend integration tests first
- Add Playwright only if it fits cleanly into the repo and validation flow
- Keep tests hermetic with seeded/local fixtures, not live broker/data dependencies

### Validation
- `pytest` integration target
- `npx playwright test` if added
- CI-friendly local command documentation

---

## Phase 6 — `mypy --strict` hardening

### Goals
- Move backend toward strict type safety incrementally
- Avoid destabilizing unrelated domains
- Start with touched/high-value modules

### Files to inspect/change first
- [`pyproject.toml`](packages/quant-engine/pyproject.toml)
- [`api`](packages/quant-engine/api)
- [`settings.py`](packages/quant-engine/config/settings.py)
- [`store.py`](packages/quant-engine/data/store.py)
- [`base.py`](packages/quant-engine/execution/base.py)
- [`base.py`](packages/quant-engine/strategies/base.py)

### Planned changes
- Do not flip global strict immediately
- Add stricter per-module mypy targets first
- Remove obvious `Any` spread in API/state/store boundaries
- Add typed protocols or narrow dataclasses where needed
- After modules are clean, raise scope gradually

### Validation
- `mypy` against selected modules
- Final stage may expand to full package if feasible

---

## Phase 7 — Execution realism + microstructure layer

### Goals
- Improve realism of backtesting/live-paper parity
- Add initial order-book-aware features
- Stay honest about scope: improved realism, not a full HFT engine rewrite

### Files to inspect/change
- [`broker.py`](packages/quant-engine/backtesting/broker.py)
- [`engine.py`](packages/quant-engine/backtesting/engine.py)
- [`paper_broker.py`](packages/quant-engine/execution/paper_broker.py)
- [`base.py`](packages/quant-engine/execution/base.py)
- [`schemas.py`](packages/quant-engine/data/schemas.py)
- [`binance_feed.py`](packages/quant-engine/data/feeds/binance_feed.py)
- [`alpaca_feed.py`](packages/quant-engine/data/feeds/alpaca_feed.py)
- [`pipeline.py`](packages/quant-engine/features/pipeline.py)
- New tests under:
  - [`tests/backtesting`](packages/quant-engine/tests/backtesting)
  - [`tests/execution`](packages/quant-engine/tests/execution)
  - [`tests/features`](packages/quant-engine/tests/features)

### Planned changes
- Add partial fills and queue-aware fill model in simulated/paper execution
- Add latency/slippage knobs
- Add order-book imbalance feature from streamed snapshots if already supported by the schema/feed paths
- Add fee/funding hooks where straightforward
- Keep scope to an initial microstructure layer, not a full tick-engine rewrite

### Validation
- Backtesting broker tests
- Execution tests
- Feature tests for order-book-derived factors

---

## Cross-phase docs and config updates

Likely touched repeatedly:
- [`README.md`](README.md)
- [`.env.example`](.env.example)
- [`aws_cloud.md`](packages/quant-engine/docs/concepts/aws_cloud.md)
- Optional new concept doc:
  - [`microstructure.md`](packages/quant-engine/docs/concepts/microstructure.md)

---

## Validation matrix by phase

### Phase 1
- Python API route tests
- Auth/RBAC tests
- Manual localhost bind verification
- Protected endpoint rejection without token

### Phase 2
- Bloomberg adapter unit tests
- Pipeline fallback tests

### Phase 3
- `terraform fmt -check`
- `terraform validate`

### Phase 4
- Portfolio API tests
- `npm run lint`
- `npm run build`

### Phase 5
- `pytest` integration suite
- Playwright suite if added

### Phase 6
- Targeted `mypy` strict passes
- Regression pytest run

### Phase 7
- Broker/execution/feature tests
- Full test suite rerun

---

## Risks and constraints

- Bloomberg implementation depends on `blpapi` availability and an actual Bloomberg environment; the adapter must remain mock-testable and optional.
- AWS IaC can be safely added without deploying anything in this session.
- Full global `mypy --strict` may require multiple passes if legacy `Any` usage is widespread.
- HFT-style improvements must remain realistic: this codebase can become more microstructure-aware and institutionally safer, but not a true production HFT plant without major architectural changes.
- Per project security policy, the platform should not rely on insecure binding, hardcoded secrets, or unauthenticated sensitive controls.

---

## Recommended execution chunks

Recommended PR-sized chunks:

1. Phase 1 only
2. Phase 2 + tests
3. Phase 3 + docs
4. Phase 4 + Phase 5
5. Phase 6
6. Phase 7

This keeps each step verifiable and reduces regression risk while moving the project toward a high-grade algorithmic AI trading bot.