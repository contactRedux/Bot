/**
 * pages/BacktestExplorer.tsx
 *
 * Configure and run backtests, then display results.
 *
 * Flow:
 *  1. User fills in form (tickers, strategies, date range, capital, interval)
 *  2. POST /api/backtest/run → gets run_id
 *  3. Poll GET /api/backtest/status/:run_id every 2 s until completed/failed
 *  4. GET /api/backtest/result/:run_id → render equity curve + metric grid + trade log
 */
import { useState, useEffect, useRef } from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { runBacktest, fetchBacktestStatus, fetchBacktestResult } from "@/lib/api";
import { runOptimize, fetchOptimizeStatus, fetchOptimizeResult, runWalkForward, fetchWalkForwardResult } from "@/lib/api";
import type { BacktestResponse, MetricsSummary } from "@/lib/types";

// ---------------------------------------------------------------------------
// Available options
// ---------------------------------------------------------------------------

const ALL_STRATEGIES = [
  "momentum",
  "mean_reversion",
  "stat_arb",
  "market_making",
  "sentiment",
  "macro_factor",
  "kelly_vol",
  "kalman_trend",
  "vwap_reversion",
];

const OPTIMIZABLE_STRATEGIES = [
  "momentum",
  "mean_reversion",
  "kelly_vol",
  "kalman_trend",
  "vwap_reversion",
];

const INTERVALS = ["1d", "1h", "15m"];

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function MetricCard({
  label,
  value,
  positive,
}: {
  label: string;
  value: string;
  positive?: boolean | null;
}) {
  const color =
    positive === null || positive === undefined
      ? "text-zinc-100"
      : positive
        ? "text-emerald-400"
        : "text-rose-400";
  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-800/60 p-3">
      <p className="text-xs text-zinc-500">{label}</p>
      <p className={`mt-1 font-mono text-lg font-semibold ${color}`}>{value}</p>
    </div>
  );
}

function ProgressBar({ pct }: { pct: number }) {
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-zinc-700">
      <div
        className="h-full bg-sky-400 transition-all duration-300"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

const CurveTooltip = ({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ value: number }>;
  label?: string;
}) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded border border-zinc-600 bg-zinc-800 px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 font-mono text-zinc-400">{label}</p>
      <p className="font-mono font-semibold text-sky-400">
        $
        {payload[0].value.toLocaleString("en-US", {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        })}
      </p>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function BacktestExplorer() {
  // Form state
  const [tickerInput, setTickerInput]   = useState("AAPL,MSFT");
  const [strategies, setStrategies]     = useState<string[]>(["momentum"]);
  const [startDate, setStartDate]       = useState("2024-01-01");
  const [endDate, setEndDate]           = useState("2026-07-01");
  const [capital, setCapital]           = useState(100_000);
  const [interval, setInterval]         = useState("1d");

  // Run state
  const [runId, setRunId]               = useState<string | null>(null);
  const [progress, setProgress]         = useState(0);
  const [statusMsg, setStatusMsg]       = useState("");
  const [running, setRunning]           = useState(false);
  const [result, setResult]             = useState<BacktestResponse | null>(null);
  const [error, setError]               = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof window.setInterval> | null>(null);

  // Stop polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current !== null) window.clearInterval(pollRef.current);
    };
  }, []);

  const stopPolling = () => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const startPolling = (id: string) => {
    const poll = async () => {
      try {
        const status = await fetchBacktestStatus(id);
        setProgress(status.progress_pct);
        setStatusMsg(status.message);

        if (status.status === "completed") {
          stopPolling();
          const res = await fetchBacktestResult(id);
          setResult(res);
          setRunning(false);
        } else if (status.status === "failed") {
          stopPolling();
          setError(`Backtest failed: ${status.message}`);
          setRunning(false);
        }
      } catch (e) {
        stopPolling();
        setError(`Polling error: ${String(e)}`);
        setRunning(false);
      }
    };
    pollRef.current = window.setInterval(() => { void poll(); }, 2_000);
  };

  const handleRun = async () => {
    setError(null);
    setResult(null);
    setProgress(0);
    setStatusMsg("");
    setRunning(true);

    const tickers = tickerInput
      .split(/[\s,]+/)
      .map((t) => t.trim().toUpperCase())
      .filter(Boolean);

    if (tickers.length === 0) {
      setError("Enter at least one ticker symbol.");
      setRunning(false);
      return;
    }

    try {
      const res = await runBacktest({
        tickers,
        start_date: startDate,
        end_date: endDate,
        strategies: strategies.length > 0 ? strategies : ["all"],
        initial_capital: capital,
        interval,
      });

      // API may return immediately if synchronous
      if (res.status === "completed") {
        setResult(res);
        setRunning(false);
        setProgress(100);
      } else {
        setRunId(res.run_id);
        startPolling(res.run_id);
      }
    } catch (e) {
      setError(`Failed to start backtest: ${String(e)}`);
      setRunning(false);
    }
  };

  const toggleStrategy = (id: string) => {
    setStrategies((prev) =>
      prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id],
    );
  };

  // Derived metric display
  const m = result?.metrics as Partial<MetricsSummary> | undefined;

  const fmt = (n: number | undefined, decimals = 2) =>
    n !== undefined && n !== null ? n.toFixed(decimals) : "—";
  const fmtUsd = (n: number | undefined) =>
    n !== undefined && n !== null
      ? n.toLocaleString("en-US", {
          style: "currency",
          currency: "USD",
          minimumFractionDigits: 0,
          maximumFractionDigits: 0,
        })
      : "—";

  const chartData =
    result?.equity_curve?.map((pt) => ({
      time: pt.timestamp.slice(0, 10),
      equity: pt.equity,
    })) ?? [];

  const attribution = result?.strategy_attribution ?? {};

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-zinc-100">Backtest Explorer</h1>
      </div>

      {/* ── Form ── */}
      <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-5">
        <h2 className="mb-4 text-sm font-semibold uppercase tracking-wider text-zinc-400">
          Configuration
        </h2>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {/* Tickers */}
          <div className="sm:col-span-2 lg:col-span-1">
            <label className="mb-1 block text-xs text-zinc-400">
              Tickers (comma separated)
            </label>
            <input
              type="text"
              value={tickerInput}
              onChange={(e) => setTickerInput(e.target.value)}
              placeholder="AAPL, MSFT, BTC-USD"
              className="w-full rounded border border-zinc-600 bg-zinc-900 px-3 py-2 font-mono text-sm text-zinc-100 placeholder-zinc-600 focus:border-sky-400 focus:outline-none"
            />
          </div>

          {/* Date range */}
          <div>
            <label className="mb-1 block text-xs text-zinc-400">Start Date</label>
            <input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="w-full rounded border border-zinc-600 bg-zinc-900 px-3 py-2 font-mono text-sm text-zinc-100 focus:border-sky-400 focus:outline-none"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs text-zinc-400">End Date</label>
            <input
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="w-full rounded border border-zinc-600 bg-zinc-900 px-3 py-2 font-mono text-sm text-zinc-100 focus:border-sky-400 focus:outline-none"
            />
          </div>

          {/* Capital */}
          <div>
            <label className="mb-1 block text-xs text-zinc-400">Initial Capital ($)</label>
            <input
              type="number"
              value={capital}
              onChange={(e) => setCapital(Number(e.target.value))}
              min={1000}
              step={10000}
              className="w-full rounded border border-zinc-600 bg-zinc-900 px-3 py-2 font-mono text-sm text-zinc-100 focus:border-sky-400 focus:outline-none"
            />
          </div>

          {/* Interval */}
          <div>
            <label className="mb-1 block text-xs text-zinc-400">Bar Interval</label>
            <select
              value={interval}
              onChange={(e) => setInterval(e.target.value)}
              className="w-full rounded border border-zinc-600 bg-zinc-900 px-3 py-2 font-mono text-sm text-zinc-100 focus:border-sky-400 focus:outline-none"
            >
              {INTERVALS.map((i) => (
                <option key={i} value={i}>
                  {i}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Strategies */}
        <div className="mt-4">
          <p className="mb-2 text-xs text-zinc-400">Strategies</p>
          <div className="flex flex-wrap gap-2">
            {ALL_STRATEGIES.map((s) => (
              <button
                key={s}
                onClick={() => toggleStrategy(s)}
                className={`rounded px-3 py-1 text-xs font-medium transition-colors ${
                  strategies.includes(s)
                    ? "bg-sky-500/20 text-sky-400 ring-1 ring-sky-400/30"
                    : "bg-zinc-700 text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {s}
              </button>
            ))}
          </div>
        </div>

        {/* Run button */}
        <div className="mt-5">
          <button
            onClick={handleRun}
            disabled={running}
            className="rounded bg-sky-500 px-6 py-2 text-sm font-semibold text-zinc-900 transition hover:bg-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {running ? "Running…" : "Run Backtest"}
          </button>
        </div>
      </div>

      {/* ── Progress ── */}
      {running && (
        <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-4">
          <div className="mb-2 flex items-center justify-between text-xs text-zinc-400">
            <span>
              {statusMsg || "Running backtest…"}{" "}
              {runId && <span className="font-mono text-zinc-600">({runId.slice(0, 8)}…)</span>}
            </span>
            <span className="font-mono">{progress.toFixed(0)}%</span>
          </div>
          <ProgressBar pct={progress} />
        </div>
      )}

      {/* ── Error ── */}
      {error && (
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-sm text-rose-400">
          {error}
        </div>
      )}

      {/* ── Results ── */}
      {result && (
        <div className="space-y-5">
          {/* Halted banner */}
          {result.halted && (
            <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-400">
              ⚠ Backtest halted early: {result.halt_reason}
            </div>
          )}

          {/* Header */}
          <div className="flex flex-wrap items-center gap-4 text-xs text-zinc-500">
            <span className="rounded-full bg-emerald-500/15 px-3 py-0.5 font-medium text-emerald-400">
              Completed
            </span>
            <span>
              {result.tickers.join(", ")} · {result.bar_interval} · $
              {result.initial_capital.toLocaleString()}
            </span>
            <span className="font-mono text-zinc-600">{result.run_id.slice(0, 12)}…</span>
          </div>

          {/* Metric cards */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            <MetricCard
              label="Total Return"
              value={`${m?.total_return_pct !== undefined && m.total_return_pct >= 0 ? "+" : ""}${fmt(m?.total_return_pct)}%`}
              positive={m?.total_return_pct !== undefined ? m.total_return_pct >= 0 : null}
            />
            <MetricCard
              label="CAGR"
              value={`${fmt(m?.cagr_pct)}%`}
              positive={m?.cagr_pct !== undefined ? m.cagr_pct >= 0 : null}
            />
            <MetricCard label="Sharpe" value={fmt(m?.sharpe_ratio)} positive={null} />
            <MetricCard label="Sortino" value={fmt(m?.sortino_ratio)} positive={null} />
            <MetricCard
              label="Max Drawdown"
              value={`-${fmt(m?.max_drawdown_pct)}%`}
              positive={false}
            />
            <MetricCard
              label="Ann. Volatility"
              value={`${fmt(m?.annual_volatility_pct)}%`}
              positive={null}
            />
            <MetricCard
              label="Win Rate"
              value={`${fmt(m?.win_rate_pct)}%`}
              positive={m?.win_rate_pct !== undefined ? m.win_rate_pct >= 50 : null}
            />
            <MetricCard label="Profit Factor" value={fmt(m?.profit_factor)} positive={null} />
            <MetricCard label="Total Trades" value={String(m?.n_trades ?? "—")} positive={null} />
            <MetricCard
              label="Final Equity"
              value={fmtUsd(m?.final_equity)}
              positive={
                m?.final_equity !== undefined
                  ? m.final_equity >= result.initial_capital
                  : null
              }
            />
          </div>

          {/* Equity curve */}
          {chartData.length > 0 && (
            <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-4">
              <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-zinc-500">
                Equity Curve
              </h3>
              <ResponsiveContainer width="100%" height={220}>
                <AreaChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                  <defs>
                    <linearGradient id="btGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#38bdf8" stopOpacity={0.25} />
                      <stop offset="95%" stopColor="#38bdf8" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#3f3f46" />
                  <XAxis
                    dataKey="time"
                    tick={{ fill: "#a1a1aa", fontSize: 10 }}
                    tickLine={false}
                    minTickGap={80}
                  />
                  <YAxis
                    domain={["auto", "auto"]}
                    tick={{ fill: "#a1a1aa", fontSize: 10 }}
                    tickLine={false}
                    width={64}
                    tickFormatter={(v: number) =>
                      v >= 1_000_000
                        ? `$${(v / 1_000_000).toFixed(1)}M`
                        : `$${(v / 1_000).toFixed(0)}k`
                    }
                  />
                  <Tooltip content={<CurveTooltip />} />
                  <Area
                    type="monotone"
                    dataKey="equity"
                    stroke="#38bdf8"
                    strokeWidth={1.5}
                    fill="url(#btGrad)"
                    dot={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Strategy attribution */}
          {Object.keys(attribution).length > 0 && (
            <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-4">
              <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-zinc-500">
                Strategy Attribution (Realised P&L)
              </h3>
              <div className="space-y-2">
                {Object.entries(attribution).map(([sid, pnl]) => {
                  const total = Math.abs(
                    Object.values(attribution).reduce((a, b) => a + Math.abs(b), 0),
                  );
                  const pct = total > 0 ? (Math.abs(pnl) / total) * 100 : 0;
                  return (
                    <div key={sid}>
                      <div className="mb-0.5 flex items-center justify-between text-xs">
                        <span className="text-zinc-400">{sid}</span>
                        <span
                          className={`font-mono font-semibold ${pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}
                        >
                          {fmtUsd(pnl)}
                        </span>
                      </div>
                      <div className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-700">
                        <div
                          className={`h-full ${pnl >= 0 ? "bg-emerald-400" : "bg-rose-400"}`}
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Trade log */}
          {result.trade_log && result.trade_log.length > 0 && (
            <div className="rounded-lg border border-zinc-700 bg-zinc-800">
              <div className="flex items-center justify-between border-b border-zinc-700 px-4 py-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
                  Trade Log
                </span>
                <span className="rounded-full bg-zinc-700 px-2 py-0.5 font-mono text-xs text-zinc-400">
                  {result.trade_log.length}
                </span>
              </div>
              <div className="max-h-96 overflow-auto">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-zinc-800">
                    <tr className="text-left text-zinc-500">
                      <th className="px-4 py-2">Time</th>
                      <th className="px-4 py-2">Ticker</th>
                      <th className="px-4 py-2">Side</th>
                      <th className="px-4 py-2 text-right">Qty</th>
                      <th className="px-4 py-2 text-right">Fill $</th>
                      <th className="px-4 py-2 text-right">Realised P&L</th>
                      <th className="px-4 py-2">Strategy</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.trade_log.map((t, i) => {
                      const side = String(t.side ?? "");
                      const isBuy = side.toUpperCase().includes("BUY");
                      const pnl = Number(t.realised_pnl ?? 0);
                      return (
                        <tr
                          key={i}
                          className="border-t border-zinc-700/50 hover:bg-zinc-700/20"
                        >
                          <td className="px-4 py-1.5 font-mono text-zinc-500">
                            {String(t.timestamp ?? "").slice(0, 16).replace("T", " ")}
                          </td>
                          <td className="px-4 py-1.5 font-mono font-semibold text-sky-400">
                            {String(t.ticker ?? "")}
                          </td>
                          <td className="px-4 py-1.5">
                            <span
                              className={`rounded px-1.5 py-0.5 font-medium ${
                                isBuy
                                  ? "bg-emerald-500/15 text-emerald-400"
                                  : "bg-rose-500/15 text-rose-400"
                              }`}
                            >
                              {side.toUpperCase()}
                            </span>
                          </td>
                          <td className="px-4 py-1.5 text-right font-mono text-zinc-300">
                            {String(t.quantity ?? "")}
                          </td>
                          <td className="px-4 py-1.5 text-right font-mono text-zinc-300">
                            ${Number(t.fill_price ?? 0).toFixed(2)}
                          </td>
                          <td
                            className={`px-4 py-1.5 text-right font-mono ${
                              pnl >= 0 ? "text-emerald-400" : "text-rose-400"
                            }`}
                          >
                            {fmtUsd(pnl)}
                          </td>
                          <td className="px-4 py-1.5 text-zinc-500">
                            {String(t.strategy_id ?? "")}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Optimizer panel ── */}
      <OptimizerPanel
        tickerInput={tickerInput}
        startDate={startDate}
        endDate={endDate}
        capital={capital}
        interval={interval}
      />

      {/* ── Walk-forward panel ── */}
      <WalkForwardPanel
        tickerInput={tickerInput}
        startDate={startDate}
        endDate={endDate}
        capital={capital}
        interval={interval}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Optimizer Panel
// ---------------------------------------------------------------------------

function OptimizerPanel({
  tickerInput,
  startDate,
  endDate,
  capital,
  interval,
}: {
  tickerInput: string;
  startDate: string;
  endDate: string;
  capital: number;
  interval: string;
}) {
  const [strategy, setStrategy] = useState("momentum");
  const [nTrials, setNTrials]   = useState(40);
  const [objective, setObjective] = useState("sharpe");
  const [busy, setBusy]         = useState(false);
  const [statusMsg, setStatusMsg] = useState("");
  const [result, setResult]     = useState<Record<string, unknown> | null>(null);
  const [error, setError]       = useState<string | null>(null);
  const pollRef                 = useRef<ReturnType<typeof window.setInterval> | null>(null);

  useEffect(() => () => { if (pollRef.current) window.clearInterval(pollRef.current); }, []);

  const handleRun = async () => {
    setError(null); setResult(null); setStatusMsg(""); setBusy(true);
    const tickers = tickerInput.split(/[\s,]+/).map(t => t.trim().toUpperCase()).filter(Boolean);
    try {
      const r = await runOptimize({ strategy, tickers, start_date: startDate, end_date: endDate, n_trials: nTrials, objective, initial_capital: capital, interval });
      const id = r.run_id as string;
      pollRef.current = window.setInterval(async () => {
        const s = await fetchOptimizeStatus(id);
        setStatusMsg((s as Record<string,unknown>).message as string ?? "");
        if ((s as Record<string,unknown>).status === "completed") {
          window.clearInterval(pollRef.current!); pollRef.current = null;
          const res = await fetchOptimizeResult(id);
          setResult(res as Record<string, unknown>); setBusy(false);
        } else if ((s as Record<string,unknown>).status === "failed") {
          window.clearInterval(pollRef.current!); pollRef.current = null;
          setError((s as Record<string,unknown>).message as string ?? "Optimization failed"); setBusy(false);
        }
      }, 2_500);
    } catch (e) { setError(String(e)); setBusy(false); }
  };

  const bestParams = result?.best_params as Record<string, number> | undefined;
  const allTrials  = (result?.all_trials as Array<Record<string,unknown>> | undefined) ?? [];

  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-5 space-y-4">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-zinc-400">
        Bayesian Parameter Optimisation (Optuna TPE)
      </h2>
      <p className="text-xs text-zinc-500">
        Finds the best strategy parameters on your backtest date range using Tree-structured
        Parzen Estimator search. Uses the same tickers / dates / capital from the form above.
      </p>

      {/* Controls */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div>
          <label className="mb-1 block text-xs text-zinc-400">Strategy</label>
          <select value={strategy} onChange={e => setStrategy(e.target.value)}
            className="w-full rounded border border-zinc-600 bg-zinc-900 px-2 py-1.5 text-sm text-zinc-100 focus:border-sky-400 focus:outline-none">
            {OPTIMIZABLE_STRATEGIES.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-400">Objective</label>
          <select value={objective} onChange={e => setObjective(e.target.value)}
            className="w-full rounded border border-zinc-600 bg-zinc-900 px-2 py-1.5 text-sm text-zinc-100 focus:border-sky-400 focus:outline-none">
            {["sharpe","sortino","calmar","total_return"].map(o => <option key={o} value={o}>{o}</option>)}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-400">Trials</label>
          <input type="number" value={nTrials} min={5} max={200} step={5}
            onChange={e => setNTrials(Number(e.target.value))}
            className="w-full rounded border border-zinc-600 bg-zinc-900 px-2 py-1.5 font-mono text-sm text-zinc-100 focus:border-sky-400 focus:outline-none" />
        </div>
        <div className="flex items-end">
          <button onClick={handleRun} disabled={busy}
            className="w-full rounded bg-violet-500 px-4 py-1.5 text-sm font-semibold text-white transition hover:bg-violet-400 disabled:cursor-not-allowed disabled:opacity-50">
            {busy ? "Optimising…" : "Run Optimisation"}
          </button>
        </div>
      </div>

      {statusMsg && <p className="text-xs text-zinc-400">{statusMsg}</p>}
      {error && <p className="text-xs text-rose-400">{error}</p>}

      {/* Best params */}
      {bestParams && Object.keys(bestParams).length > 0 && (
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">
            Best Parameters — {objective} = {Number(result?.best_value).toFixed(4)}
          </p>
          <div className="flex flex-wrap gap-2">
            {Object.entries(bestParams).map(([k, v]) => (
              <span key={k} className="rounded border border-zinc-600 bg-zinc-900 px-2 py-1 font-mono text-xs text-zinc-200">
                <span className="text-zinc-500">{k}:</span> {typeof v === "number" ? (Number.isInteger(v) ? v : v.toFixed(4)) : String(v)}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Trial chart */}
      {allTrials.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">
            Trial Values ({allTrials.length} trials)
          </p>
          <ResponsiveContainer width="100%" height={140}>
            <AreaChart data={allTrials.filter(t => t.value != null).map((t, i) => ({ i, value: t.value as number }))}
              margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="optGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#a78bfa" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#a78bfa" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#3f3f46" />
              <XAxis dataKey="i" tick={{ fill: "#71717a", fontSize: 9 }} tickLine={false} />
              <YAxis tick={{ fill: "#71717a", fontSize: 9 }} tickLine={false} width={40} />
              <Tooltip formatter={(v: number) => v.toFixed(4)} contentStyle={{ background: "#27272a", border: "1px solid #3f3f46", fontSize: 11 }} />
              <Area type="monotone" dataKey="value" stroke="#a78bfa" strokeWidth={1.5} fill="url(#optGrad)" dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Walk-Forward Panel
// ---------------------------------------------------------------------------

function WalkForwardPanel({
  tickerInput,
  startDate,
  endDate,
  capital,
  interval,
}: {
  tickerInput: string;
  startDate: string;
  endDate: string;
  capital: number;
  interval: string;
}) {
  const [wfStrategies, setWfStrategies] = useState<string[]>(["momentum", "mean_reversion"]);
  const [nSplits, setNSplits]           = useState(4);
  const [oosDays, setOosDays]           = useState(252);
  const [busy, setBusy]                 = useState(false);
  const [statusMsg, setStatusMsg]       = useState("");
  const [result, setResult]             = useState<Record<string, unknown> | null>(null);
  const [error, setError]               = useState<string | null>(null);
  const pollRef                         = useRef<ReturnType<typeof window.setInterval> | null>(null);

  useEffect(() => () => { if (pollRef.current) window.clearInterval(pollRef.current); }, []);

  const toggleWfStrategy = (s: string) =>
    setWfStrategies(prev => prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s]);

  const handleRun = async () => {
    setError(null); setResult(null); setStatusMsg(""); setBusy(true);
    const tickers = tickerInput.split(/[\s,]+/).map(t => t.trim().toUpperCase()).filter(Boolean);
    try {
      const r = await runWalkForward({ strategies: wfStrategies, tickers, start_date: startDate, end_date: endDate, n_splits: nSplits, oos_size_days: oosDays, initial_capital: capital, interval });
      const id = r.run_id as string;
      pollRef.current = window.setInterval(async () => {
        const s = await fetchOptimizeStatus(id);
        setStatusMsg((s as Record<string,unknown>).message as string ?? "");
        if ((s as Record<string,unknown>).status === "completed") {
          window.clearInterval(pollRef.current!); pollRef.current = null;
          const res = await fetchWalkForwardResult(id);
          setResult(res as Record<string, unknown>); setBusy(false);
        } else if ((s as Record<string,unknown>).status === "failed") {
          window.clearInterval(pollRef.current!); pollRef.current = null;
          setError((s as Record<string,unknown>).message as string ?? "Walk-forward failed"); setBusy(false);
        }
      }, 2_500);
    } catch (e) { setError(String(e)); setBusy(false); }
  };

  const folds = (result?.folds as Array<Record<string,unknown>> | undefined) ?? [];
  const agg   = result?.aggregate_metrics as Record<string, Record<string,number>> | undefined;

  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-5 space-y-4">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-zinc-400">
        Walk-Forward Out-of-Sample Validation
      </h2>
      <p className="text-xs text-zinc-500">
        Repeatedly trains on expanding in-sample windows and evaluates on the next OOS fold.
        Prevents look-ahead bias and overfitting — this is how hedge funds validate strategies.
      </p>

      {/* Strategy selection */}
      <div>
        <p className="mb-2 text-xs text-zinc-400">Strategies</p>
        <div className="flex flex-wrap gap-2">
          {ALL_STRATEGIES.map(s => (
            <button key={s} onClick={() => toggleWfStrategy(s)}
              className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                wfStrategies.includes(s) ? "bg-sky-500/20 text-sky-400 ring-1 ring-sky-400/30" : "bg-zinc-700 text-zinc-400 hover:text-zinc-200"
              }`}>{s}</button>
          ))}
        </div>
      </div>

      {/* Controls */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div>
          <label className="mb-1 block text-xs text-zinc-400">OOS Folds</label>
          <input type="number" value={nSplits} min={2} max={10}
            onChange={e => setNSplits(Number(e.target.value))}
            className="w-full rounded border border-zinc-600 bg-zinc-900 px-2 py-1.5 font-mono text-sm text-zinc-100 focus:border-sky-400 focus:outline-none" />
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-400">OOS Days / Fold</label>
          <input type="number" value={oosDays} min={63} max={756} step={21}
            onChange={e => setOosDays(Number(e.target.value))}
            className="w-full rounded border border-zinc-600 bg-zinc-900 px-2 py-1.5 font-mono text-sm text-zinc-100 focus:border-sky-400 focus:outline-none" />
        </div>
        <div className="sm:col-span-2 flex items-end">
          <button onClick={handleRun} disabled={busy || wfStrategies.length === 0}
            className="w-full rounded bg-sky-600 px-4 py-1.5 text-sm font-semibold text-white transition hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-50">
            {busy ? "Running folds…" : "Run Walk-Forward"}
          </button>
        </div>
      </div>

      {statusMsg && <p className="text-xs text-zinc-400">{statusMsg}</p>}
      {error && <p className="text-xs text-rose-400">{error}</p>}

      {/* Aggregate metrics */}
      {agg && (
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">
            Aggregate OOS Metrics (mean ± std across {folds.length} folds)
          </p>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
            {(["sharpe_ratio","total_return_pct","max_drawdown_pct","win_rate_pct","profit_factor"] as const).map(k => {
              const d = agg[k];
              if (!d) return null;
              return (
                <div key={k} className="rounded border border-zinc-700 bg-zinc-900 p-2 text-center">
                  <p className="text-xs text-zinc-500">{k.replace(/_/g, " ")}</p>
                  <p className="font-mono text-sm font-semibold text-zinc-100">{d.mean.toFixed(2)}</p>
                  <p className="font-mono text-xs text-zinc-600">±{d.std.toFixed(2)}</p>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Per-fold table */}
      {folds.length > 0 && (
        <div className="overflow-hidden rounded border border-zinc-700">
          <table className="w-full text-xs">
            <thead className="bg-zinc-900">
              <tr className="text-left text-zinc-500">
                <th className="px-3 py-2">Fold</th>
                <th className="px-3 py-2">OOS Start</th>
                <th className="px-3 py-2">OOS End</th>
                <th className="px-3 py-2 text-right">Return%</th>
                <th className="px-3 py-2 text-right">Sharpe</th>
                <th className="px-3 py-2 text-right">MaxDD%</th>
                <th className="px-3 py-2 text-right">Trades</th>
                <th className="px-3 py-2 text-right">Win%</th>
              </tr>
            </thead>
            <tbody>
              {folds.map((f, i) => {
                const m = f.metrics as Record<string, number> | undefined;
                const ret = m?.total_return_pct ?? 0;
                return (
                  <tr key={i} className="border-t border-zinc-700/50 hover:bg-zinc-700/20">
                    <td className="px-3 py-1.5 font-mono font-semibold text-zinc-400">F{i + 1}</td>
                    <td className="px-3 py-1.5 font-mono text-zinc-500">{String(f.oos_start ?? "").slice(0, 10)}</td>
                    <td className="px-3 py-1.5 font-mono text-zinc-500">{String(f.oos_end ?? "").slice(0, 10)}</td>
                    <td className={`px-3 py-1.5 text-right font-mono ${ret >= 0 ? "text-emerald-400" : "text-rose-400"}`}>{ret.toFixed(2)}%</td>
                    <td className="px-3 py-1.5 text-right font-mono text-zinc-300">{(m?.sharpe_ratio ?? 0).toFixed(3)}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-rose-400">-{(m?.max_drawdown_pct ?? 0).toFixed(2)}%</td>
                    <td className="px-3 py-1.5 text-right font-mono text-zinc-400">{m?.n_trades ?? 0}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-zinc-400">{(m?.win_rate_pct ?? 0).toFixed(1)}%</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
