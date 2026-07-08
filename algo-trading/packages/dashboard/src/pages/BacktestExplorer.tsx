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
      <h1 className="text-xl font-semibold text-zinc-100">Backtest Explorer</h1>

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
    </div>
  );
}
