# Handoff — Next Session

**Purpose:** Complete context for continuing the algo-trading platform in a fresh task window.
**Goal:** Implement Phase 5 (integration/E2E tests), Phase 6 (mypy hardening), Phase 7 (execution realism), then run extensive testing across all suites.

---

## Repository location

```
/Users/henrynguyen/Bot/algo-trading/
```

All work is inside `algo-trading/`. The repo root also contains a `README.md` and this `HANDOFF.md`.

---

## Current validated state

```
make test        →  567 passed, 3 skipped, 0 failed  (non-model suite)
make test-models →  37 passed, 0 failed               (ML model suite)
npm run lint     →  clean (0 warnings/errors)
npm run build    →  clean build, 827ms
```

**Python interpreter:** `python3.11` (deps installed system-wide under `/opt/homebrew/lib/python3.11`). The `Makefile` resolves `python3.11` before `python3`. No virtualenv — all packages installed globally under `python3.11`.

**Node.js:** Standard npm — `node_modules/` exists at `packages/dashboard/`.

---

## What has been built (all phases complete)

### Original sub-tasks (1–11) — all complete

| Sub-task | What it built |
|----------|--------------|
| 1 | Monorepo scaffold, `.env.example`, `pyproject.toml`, `Makefile`, Vite/React scaffold |
| 2 | 8→9 data feed adapters (added Bloomberg), `DataStore` (SQLAlchemy), `DataPipeline` |
| 3 | `FeaturePipeline` — 40 technical indicators + statistical + sentiment + macro |
| 4 | LSTM, Transformer, GP, LightGBM, PPO RL, Ensemble models + `ModelRegistry` + walk-forward CV |
| 5 | 6 strategy families + `StrategyOrchestrator` |
| 6 | Event-driven `BacktestEngine` + `SimulatedBroker` + `BacktestReport` + CLI runner |
| 7 | `RiskManager` (6-stage gate) + `VaR/CVaR` + `DrawdownMonitor` + correlation checker |
| 8 | `PaperBroker` + `AlpacaBroker` + `BinanceBroker` + `BrokerFactory` |
| 9 | FastAPI REST + WebSocket server, all route modules |
| 10 | React dashboard (Overview, Backtest, Live, News, Risk pages) + Zustand stores + WS hook |
| 11 | 9 concept docs in `docs/concepts/` |

### Institutional-grade upgrade phases (1–4) — all complete

| Phase | What it built |
|-------|--------------|
| 1 — Security | localhost binding, `require_operator` OIDC seam, RBAC on 4 mutation endpoints, AUDIT log lines |
| 2 — Bloomberg | `bloomberg_feed.py` (optional `blpapi`), priority policy in pipeline, sentiment quality weights |
| 3 — AWS IaC | 11 Terraform files: VPC, ECR, ECS Fargate, RDS PG16, S3, Secrets Manager, IAM, CloudWatch |
| 4 — PriceChart | `GET /api/portfolio/price-history`, `DataStore` wired into `AppState`, `Overview.tsx` live data |

---

## File tree highlights (most relevant files for next phases)

```
algo-trading/
├── Makefile                          ← python3.11, all targets
├── .env.example                      ← all env vars documented
├── next-steps.md                     ← phases 5-7 fully spec'd (READ THIS FIRST)
├── HANDOFF.md                        ← this file
├── infra/terraform/                  ← 11 .tf files (Phase 3, done)
└── packages/
    ├── quant-engine/
    │   ├── api/
    │   │   ├── deps.py               ← AppState (all fields incl. data_store), require_operator
    │   │   ├── main.py               ← lifespan: DataStore init, all component init
    │   │   ├── schemas.py            ← PriceHistoryPoint/Response + all other schemas
    │   │   └── routes/
    │   │       ├── portfolio.py      ← GET /price-history endpoint (Phase 4)
    │   │       ├── backtest.py       ← require_operator on run/delete
    │   │       ├── risk.py           ← require_operator on resume
    │   │       └── strategies.py     ← require_operator on toggle
    │   ├── config/
    │   │   └── settings.py           ← all settings incl. OIDC, Bloomberg, AWS vars
    │   ├── data/
    │   │   ├── feeds/bloomberg_feed.py  ← Phase 2 (optional blpapi)
    │   │   ├── pipeline.py           ← Bloomberg-first priority policy
    │   │   └── store.py              ← DataStore (accept connect_args/poolclass)
    │   ├── backtesting/
    │   │   ├── broker.py             ← SimulatedBroker (Phase 7 target)
    │   │   └── engine.py             ← BacktestEngine (Phase 7 target)
    │   ├── execution/
    │   │   ├── base.py               ← ExecutionBroker ABC (Phase 6+7 target)
    │   │   └── paper_broker.py       ← PaperBroker (Phase 7 target)
    │   ├── features/
    │   │   └── pipeline.py           ← FeaturePipeline (Phase 7: order-book imbalance)
    │   ├── pyproject.toml            ← [bloomberg] optional dep group
    │   └── tests/
    │       ├── api/
    │       │   ├── conftest.py       ← AppState fixture with StaticPool DataStore
    │       │   ├── test_auth.py      ← 10 Phase 1 auth tests
    │       │   └── test_portfolio.py ← 14 Phase 4 price-history tests
    │       ├── data/
    │       │   └── test_bloomberg_feed.py ← 15 Phase 2 tests
    │       └── integration/          ← DOES NOT EXIST YET — create in Phase 5
    └── dashboard/
        └── src/
            ├── lib/
            │   ├── api.ts            ← fetchPriceHistory added (Phase 4)
            │   └── types.ts          ← PriceHistoryPoint/Response added (Phase 4)
            └── pages/
                └── Overview.tsx      ← useQuery + fetchPriceHistory (Phase 4)
```

---

## Phase 5 — Integration/E2E suite

### What to build

**1. Backend integration tests — `tests/integration/`**

Create `packages/quant-engine/tests/integration/__init__.py` (empty).

Create `tests/integration/test_api_app_state.py`:
- Import `TestClient` and `app` from `api.main`
- Use `TestClient(app)` as a context manager (let lifespan run fully)
- Test: `GET /health` → 200, shape has `status`, `trading_mode`, `broker_connected`
- Test: `GET /` → 200, response has `name` = `"quant-engine API"`
- Test: `GET /api/strategies` → 200, `strategies` list non-empty
- Test: `GET /api/risk/status` → 200, has `halted`, `var_95`, `current_drawdown_pct`
- Test: `GET /api/portfolio` → 200, has `cash`, `positions`
- Test: WebSocket heartbeat — connect to `/ws/feed`, receive first message within 5s, verify `event_type == "heartbeat"`

Create `tests/integration/test_portfolio_chart_flow.py`:
- Full flow: seed DataStore with 10 recent AAPL bars, call `GET /api/portfolio/price-history?ticker=AAPL`, assert 10 points returned with correct shape
- Assert `GET /api/portfolio/history` returns `equity_history` list
- Assert `GET /api/portfolio/trades` returns `trades` list
- Assert `POST /api/backtest/run` → 200 → poll `GET /api/backtest/status/{run_id}` → result has `equity_curve`

**2. Makefile targets**

Add to `Makefile`:
```makefile
test-integration:
    @echo "→ Running integration tests..."
    cd $(QE) && $(PYTHON) -m pytest tests/integration/ -v --tb=short
```

**3. Dashboard E2E (Playwright)**

In `packages/dashboard/`:
- `npm install --save-dev @playwright/test`
- Create `playwright.config.ts` (baseURL `http://localhost:5173`, Chromium only, 30s timeout)
- Create `tests/e2e/overview.spec.ts` — page load, heading visible, no console errors
- Add to `Makefile`:
  ```makefile
  test-e2e:
      @echo "→ Running Playwright E2E tests (requires running dev server)..."
      cd $(DASH) && npx playwright test
  ```

### Key technical notes
- Integration tests that use `TestClient(app)` as a full context manager will trigger the real lifespan (including DataStore init to `sqlite:///./algo_trading.db`). This is fine — the SQLite file will be created in `packages/quant-engine/` and can be `.gitignore`'d.
- WebSocket test: use `websockets` library (already installed) or `starlette.testclient.TestClient` with `with client.websocket_connect("/ws/feed") as ws:`.
- Backtest integration test: run with `--tickers AAPL --strategies momentum --start 2023-01-01 --end 2023-03-01` to keep it fast (2 months of yfinance data).

---

## Phase 6 — mypy --strict hardening

### What to build

**1. `pyproject.toml` — add per-module overrides**

In `[tool.mypy]`, add:
```toml
[[tool.mypy.overrides]]
module = ["api.*", "config.settings", "data.store", "execution.base", "strategies.base"]
disallow_untyped_defs = true
warn_return_any = true
```

**2. Fix type errors module by module**

Primary targets and known issues:

| Module | Known `Any` usage to fix |
|--------|--------------------------|
| `api/deps.py` | `AppState` fields all typed as `Any` → narrow to concrete types or `Protocol` |
| `api/routes/*.py` | Return type annotations use `dict` without key types |
| `api/main.py` | `app_settings` used as `Any` in lifespan |
| `data/store.py` | `engine_kwargs: dict` → `dict[str, Any]` is fine, but function sigs need annotations |
| `execution/base.py` | `get_account()` returns `dict[str, Any]` — acceptable |
| `strategies/base.py` | `TickerState` fields typed as `Any` in some places |

**3. Run mypy**
```bash
cd packages/quant-engine
python3.11 -m mypy api/ config/settings.py data/store.py execution/base.py strategies/base.py
```

**Don't:** flip `strict = true` globally — it will break 100+ files.
**Do:** fix the targeted modules listed above to zero errors, then verify `make test` still passes.

---

## Phase 7 — Execution realism + microstructure layer

### What to build

**1. Partial fills in `backtesting/broker.py`**

Add to `SimulatedBroker.__init__`:
- `volume_participation_rate: float = 0.05` — max fraction of bar volume to fill per bar
- `min_fill_pct: float = 0.1` — minimum partial fill to avoid dust

In `_execute_market_order`:
```python
max_fillable = bar.volume * self.volume_participation_rate
fill_qty = min(order.quantity, max_fillable)
if fill_qty < order.quantity * self.min_fill_pct:
    # Re-queue as partial
    ...
```

**2. Limit order price-check in `backtesting/broker.py`**

Current implementation fills limit orders if the price crosses. Verify and strengthen:
- Buy limit: fill only if `bar.low <= limit_price` (price dipped to your level)
- Sell limit: fill only if `bar.high >= limit_price` (price rose to your level)

**3. Slippage model extension in `backtesting/broker.py`**

Add `SlippageModel` enum (`fixed_bps`, `sqrt_impact`) and `sqrt_impact` implementation:
```python
def _calc_sqrt_slippage(self, qty: float, avg_daily_vol: float) -> float:
    """Square-root market impact: slippage ∝ sqrt(qty / ADV)."""
    if avg_daily_vol <= 0:
        return self._fixed_slippage_pct
    return self._impact_coeff * math.sqrt(qty / avg_daily_vol)
```

**4. PaperBroker partial fills in `execution/paper_broker.py`**

Mirror the `volume_participation_rate` logic from the backtesting broker. PaperBroker currently gives instant full fills — add:
- `partial_fill_mode: bool = False` (off by default for backward compat)
- When enabled, fill `min(qty, simulated_volume * rate)` and return `OrderStatus.PARTIAL`

**5. Order-book imbalance feature in `features/pipeline.py`**

```python
def _compute_order_book_imbalance(order_book: OrderBook | None) -> float:
    """(bid_vol - ask_vol) / (bid_vol + ask_vol). Returns NaN if no data."""
    if order_book is None:
        return float("nan")
    bid_vol = sum(level.size for level in order_book.bids[:5])
    ask_vol = sum(level.size for level in order_book.asks[:5])
    total = bid_vol + ask_vol
    return (bid_vol - ask_vol) / total if total > 0 else float("nan")
```

Add to `FeaturePipeline.build()` output dict as `"order_book_imbalance"`.

**6. Tests to write**

- `tests/backtesting/test_partial_fills.py` — market order with low volume bar → partial fill; re-queued remainder fills next bar
- `tests/backtesting/test_slippage.py` — sqrt_impact slippage increases with order size
- `tests/execution/test_paper_broker_partial.py` — partial fill mode returns PARTIAL status
- `tests/features/test_order_book_imbalance.py` — mock `OrderBook`, verify imbalance ∈ [-1, 1]; None order book returns NaN

---

## Extensive testing checklist (after all phases)

Run these in order after Phase 7 is complete:

```bash
# 1. Full non-model suite
make test
# Expected: 600+ passed, 0 failed

# 2. Model suite (slow, ~30s, macOS OpenMP isolation)
make test-models
# Expected: 37 passed, 0 failed

# 3. Integration tests (new in Phase 5)
cd packages/quant-engine
python3.11 -m pytest tests/integration/ -v --tb=short
# Expected: all pass

# 4. Coverage report (identify untested paths)
make test-cov
# Open htmlcov/index.html — target >80% on api/, execution/, backtesting/

# 5. mypy on targeted modules (Phase 6)
cd packages/quant-engine
python3.11 -m mypy api/ config/settings.py data/store.py execution/base.py strategies/base.py
# Expected: 0 errors

# 6. Python lint on all touched files
cd packages/quant-engine
python3.11 -m ruff check api/ config/ data/ execution/ features/ backtesting/ tests/
# Expected: 0 errors on newly written files

# 7. Frontend
cd packages/dashboard
npm run lint && npm run build
# Expected: 0 ESLint warnings, successful build

# 8. E2E (Phase 5, requires running dev server in separate terminal)
# Terminal 1: make dev
# Terminal 2:
cd packages/dashboard
npx playwright test
# Expected: all specs pass
```

---

## Known technical constraints to keep in mind

### SQLite + StaticPool pattern (Phase 4, already implemented)
Tests that write to `DataStore` and then read through the API **must** use `StaticPool`:
```python
from sqlalchemy.pool import StaticPool
store = DataStore("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
```
Without `StaticPool`, each SQLAlchemy session opens a separate connection and sees an empty in-memory database. The `conftest.py` at `tests/api/conftest.py` already does this correctly — replicate the pattern for any new test fixtures.

### macOS OpenMP conflict
`make test-models` runs as two separate `pytest` invocations:
1. LightGBM tests (no PyTorch imports)
2. PyTorch/GP/Ensemble tests (no LightGBM imports)

Don't mix them in a single invocation on macOS arm64. The `Makefile` handles this already.

### Lifespan guard in tests
`api/main.py` lifespan checks `if not hasattr(app.state, "app_state")` before setting state. When `app.state.app_state = state` is set before `TestClient(app).__enter__`, the lifespan's own AppState creation is discarded and your injected state is used. This is the correct pattern for unit tests (fast, no DB on disk). Integration tests should NOT pre-set `app.state.app_state` — let the lifespan run fully.

### OIDC auth seam
All mutation endpoints (`POST /api/backtest/run`, `DELETE /api/backtest/{run_id}`, `PATCH /api/strategies/{id}`, `POST /api/risk/resume`) check `require_operator`. In dev mode (`OIDC_ISSUER_URL` unset), this is a no-op. Tests don't need auth headers. In production, set `OIDC_ISSUER_URL` + `OIDC_AUDIENCE` in `.env`.

### Bloomberg feed
`blpapi` is not installed — `BloombergFeed.is_available()` returns `False`. All Bloomberg-dependent code paths are guarded. The data pipeline falls back to yfinance/Alpaca/Binance gracefully. Do not add `blpapi` as a hard dependency.

### Terraform
`terraform` binary is not installed locally. Validate HCL by reading the files — syntax has been manually reviewed. To validate properly: `brew install terraform && terraform init && terraform validate` in `infra/terraform/`.

---

## Environment variables cheat sheet

All in `algo-trading/.env` (copied from `.env.example`). Key ones:

| Variable | For what | Value for local dev |
|----------|----------|---------------------|
| `TRADING_MODE` | Broker selection | `dev` |
| `DATABASE_URL` | DataStore | `sqlite:///./algo_trading.db` |
| `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` | Paper/live equities | paper keys from alpaca.markets |
| `BINANCE_API_KEY` + `BINANCE_SECRET_KEY` | Crypto | testnet keys from testnet.binance.vision |
| `BINANCE_TESTNET` | Binance env | `true` |
| `NEWSAPI_KEY` | Sentiment | free key from newsapi.org |
| `ALPHA_VANTAGE_KEY` | Fundamentals | free key from alphavantage.co |
| `BLOOMBERG_APP_NAME` | Bloomberg feed | leave empty (blpapi not installed) |
| `OIDC_ISSUER_URL` | Auth | leave empty (auth skipped in dev) |
| `VITE_API_BASE_URL` | Dashboard → API | `http://localhost:8000` |
| `VITE_WS_URL` | Dashboard → WS | `ws://localhost:8000/ws/feed` |

---

## Quick-start commands

```bash
cd /Users/henrynguyen/Bot/algo-trading

# Verify current state
make test          # should show 567 passed
make test-models   # should show 37 passed

# Start platform
make dev           # API :8000 + dashboard :5173

# Run backtest
make backtest ARGS="--tickers AAPL MSFT --strategies momentum mean_reversion --start 2023-01-01 --end 2024-01-01"

# Lint all touched files
cd packages/quant-engine && python3.11 -m ruff check api/ config/ data/ && cd ../dashboard && npm run lint
```

---

## Success criteria for next session

The session is complete when ALL of the following are true:

- [ ] `make test` → 600+ passed, 0 failed (includes Phase 5 integration tests)
- [ ] `make test-models` → 37 passed, 0 failed
- [ ] `pytest tests/integration/` → all pass
- [ ] `npx playwright test` → all E2E specs pass (or documented skip if server startup is infeasible)
- [ ] `mypy api/ config/settings.py data/store.py execution/base.py strategies/base.py` → 0 errors
- [ ] `python3.11 -m ruff check` → 0 errors on all touched files
- [ ] `npm run lint && npm run build` → clean
- [ ] Phase 7 partial fill tests exist and pass
- [ ] Phase 7 order-book imbalance feature exists and has tests
- [ ] `next-steps.md` updated to mark phases 5–7 complete
