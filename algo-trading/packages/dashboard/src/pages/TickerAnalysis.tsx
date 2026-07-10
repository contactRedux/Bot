/**
 * pages/TickerAnalysis.tsx — Composite technical analysis for any ticker.
 *
 * Features:
 *  - Search any ticker; GET /api/analysis/{ticker} returns composite rating,
 *    RSI, MACD, Bollinger, MA signals, confidence %, and reasoning bullets.
 *  - Rating badge: Strong Buy / Buy / Hold / Sell / Strong Sell
 *  - Indicator grid: RSI gauge, MACD values, Bollinger, SMAs, ATR
 *  - Signal score bar chart (horizontal bars -1..+1)
 *  - Reasoning list
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchAnalysis } from "@/lib/api";
import type { AnalysisResponse } from "@/lib/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function ratingColor(r: AnalysisResponse["rating"]) {
  switch (r) {
    case "Strong Buy":  return "text-emerald-400 bg-emerald-400/10 border-emerald-500/30";
    case "Buy":         return "text-emerald-300 bg-emerald-300/10 border-emerald-400/30";
    case "Hold":        return "text-zinc-300 bg-zinc-700 border-zinc-600";
    case "Sell":        return "text-rose-300 bg-rose-300/10 border-rose-400/30";
    case "Strong Sell": return "text-rose-400 bg-rose-400/10 border-rose-500/30";
  }
}

function scoreColor(score: number) {
  if (score >= 0.5)  return "bg-emerald-500";
  if (score >= 0.1)  return "bg-emerald-400";
  if (score <= -0.5) return "bg-rose-500";
  if (score <= -0.1) return "bg-rose-400";
  return "bg-zinc-500";
}

function fmt(n: number, decimals = 2) {
  return n.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function pctBadge(v: number) {
  return (
    <span className={v >= 0 ? "text-emerald-400" : "text-rose-400"}>
      {v >= 0 ? "+" : ""}{fmt(v)}%
    </span>
  );
}

// ---------------------------------------------------------------------------
// Signal bar
// ---------------------------------------------------------------------------
function SignalBar({ label, score }: { label: string; score: number }) {
  // Score is in [-1, +1]; render as a centred bar
  const pct = Math.abs(score) * 50; // half-bar width in %
  const isPositive = score >= 0;
  return (
    <div className="flex items-center gap-3">
      <span className="w-28 shrink-0 text-xs text-zinc-400">{label}</span>
      <div className="relative flex h-4 flex-1 items-center rounded bg-zinc-800">
        {/* Centre line */}
        <div className="absolute left-1/2 h-full w-px bg-zinc-600" />
        {/* Bar */}
        <div
          className={`absolute h-3 rounded ${scoreColor(score)}`}
          style={{
            width: `${pct}%`,
            [isPositive ? "left" : "right"]: "50%",
          }}
        />
      </div>
      <span className={`w-10 text-right text-xs ${score >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
        {score >= 0 ? "+" : ""}{fmt(score, 2)}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// RSI gauge (simple arc-less text gauge)
// ---------------------------------------------------------------------------
function RsiGauge({ rsi }: { rsi: number }) {
  const color =
    rsi < 30 ? "text-emerald-400"
    : rsi > 70 ? "text-rose-400"
    : "text-zinc-200";
  const label = rsi < 30 ? "Oversold" : rsi > 70 ? "Overbought" : "Neutral";
  const barPct = Math.min(100, Math.max(0, rsi));
  const barColor = rsi < 30 ? "bg-emerald-500" : rsi > 70 ? "bg-rose-500" : "bg-sky-500";
  return (
    <div className="flex flex-col gap-1">
      <div className="flex justify-between text-xs text-zinc-500">
        <span>0</span><span>50</span><span>100</span>
      </div>
      <div className="relative h-3 rounded bg-zinc-800">
        <div className={`absolute left-0 h-3 rounded ${barColor}`} style={{ width: `${barPct}%` }} />
        {/* Oversold / overbought zone markers */}
        <div className="absolute left-[30%] h-3 w-px bg-emerald-700 opacity-60" />
        <div className="absolute left-[70%] h-3 w-px bg-rose-700 opacity-60" />
      </div>
      <div className="flex justify-between">
        <span className={`text-lg font-bold ${color}`}>{fmt(rsi, 1)}</span>
        <span className={`text-sm ${color}`}>{label}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
export default function TickerAnalysis() {
  const [input, setInput]     = useState("");
  const [ticker, setTicker]   = useState<string | null>(null);

  const { data, isFetching, error } = useQuery({
    queryKey: ["analysis", ticker],
    queryFn: () => fetchAnalysis(ticker!),
    enabled: !!ticker,
    staleTime: 60_000,
    retry: false,
  });

  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    const t = input.trim().toUpperCase();
    if (t) setTicker(t);
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-xl font-semibold text-zinc-100">Ticker Analysis</h1>
        <p className="text-sm text-zinc-500">
          Technical indicator composite for any equity or crypto ticker.
        </p>
      </div>

      {/* Search bar */}
      <form onSubmit={handleSearch} className="flex gap-3">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value.toUpperCase())}
          placeholder="Enter ticker — e.g. AMD, MU, SNDK, BTC-USD"
          className="flex-1 rounded border border-zinc-600 bg-zinc-800 px-4 py-2.5 text-sm text-zinc-100 placeholder-zinc-500 focus:border-sky-500 focus:outline-none"
        />
        <button
          type="submit"
          disabled={isFetching}
          className="rounded bg-sky-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50"
        >
          {isFetching ? "Analysing…" : "Analyse"}
        </button>
      </form>

      {/* Error */}
      {error && (
        <div className="rounded border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {(error as Error).message}
        </div>
      )}

      {/* Results */}
      {data && (
        <div className="grid gap-4 lg:grid-cols-3">

          {/* ── Rating card ─────────────────────────────────────────── */}
          <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-5 lg:col-span-1">
            <div className="mb-1 text-xs font-medium uppercase tracking-wider text-zinc-500">
              {data.ticker} · Composite Rating
            </div>
            <div className={`mt-3 inline-block rounded-lg border px-5 py-3 text-2xl font-bold ${ratingColor(data.rating)}`}>
              {data.rating}
            </div>
            <div className="mt-4 space-y-1 text-sm">
              <div className="flex justify-between">
                <span className="text-zinc-500">Composite score</span>
                <span className={data.composite_score >= 0 ? "text-emerald-400" : "text-rose-400"}>
                  {data.composite_score >= 0 ? "+" : ""}{fmt(data.composite_score, 3)}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-500">Confidence</span>
                <span className="text-zinc-200">{fmt(data.confidence_pct, 1)}%</span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-500">Last price</span>
                <span className="text-zinc-200">${fmt(data.price_stats.last_price, 2)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-500">1-day change</span>
                {pctBadge(data.price_stats.pct_change_1d)}
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-500">1-month change</span>
                {pctBadge(data.price_stats.pct_change_1m)}
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-500">Bars used</span>
                <span className="text-zinc-400">{data.bar_count} daily</span>
              </div>
            </div>
          </div>

          {/* ── Indicators + Signals ────────────────────────────────── */}
          <div className="space-y-4 lg:col-span-2">

            {/* RSI */}
            <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-4">
              <div className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">RSI (14)</div>
              <RsiGauge rsi={data.indicators.rsi} />
            </div>

            {/* Signal scores */}
            <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-4">
              <div className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">Signal Scores</div>
              <div className="space-y-2">
                <SignalBar label="MACD"         score={data.signal_scores.macd} />
                <SignalBar label="MA Trend"     score={data.signal_scores.ma_trend} />
                <SignalBar label="RSI"          score={data.signal_scores.rsi} />
                <SignalBar label="Bollinger"    score={data.signal_scores.bollinger} />
                <SignalBar label="Short Trend"  score={data.signal_scores.short_trend} />
              </div>
            </div>
          </div>

          {/* ── Indicators grid ─────────────────────────────────────── */}
          <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-4 lg:col-span-2">
            <div className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">Technical Indicators</div>
            <div className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm sm:grid-cols-3">
              {[
                ["MACD Line",    fmt(data.indicators.macd_line, 4)],
                ["MACD Signal",  fmt(data.indicators.macd_signal, 4)],
                ["BB Upper",     `$${fmt(data.indicators.bb_upper)}`],
                ["BB Mid",       `$${fmt(data.indicators.bb_mid)}`],
                ["BB Lower",     `$${fmt(data.indicators.bb_lower)}`],
                ["SMA 20",       `$${fmt(data.indicators.sma_20)}`],
                ["SMA 50",       `$${fmt(data.indicators.sma_50)}`],
                ["SMA 200",      `$${fmt(data.indicators.sma_200)}`],
                ["ATR",          fmt(data.indicators.atr, 4)],
              ].map(([label, val]) => (
                <div key={label} className="flex justify-between border-b border-zinc-700/50 py-1.5">
                  <span className="text-zinc-500">{label}</span>
                  <span className="text-zinc-200">{val}</span>
                </div>
              ))}
            </div>
          </div>

          {/* ── Reasoning ───────────────────────────────────────────── */}
          <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-4 lg:col-span-1">
            <div className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">Reasoning</div>
            <ul className="space-y-2">
              {data.reasoning.map((r, i) => (
                <li key={i} className="flex gap-2 text-sm text-zinc-300">
                  <span className="mt-0.5 shrink-0 text-sky-500">›</span>
                  {r}
                </li>
              ))}
            </ul>
            <p className="mt-4 text-xs text-zinc-600">
              As of {new Date(data.as_of).toLocaleString()}. For informational purposes only.
            </p>
          </div>
        </div>
      )}

      {/* Empty state */}
      {!data && !isFetching && !error && (
        <div className="rounded-lg border border-zinc-700 bg-zinc-800/50 py-20 text-center">
          <p className="text-sm text-zinc-500">
            Enter a ticker above to see a composite technical analysis.
          </p>
          <p className="mt-1 text-xs text-zinc-600">
            Works for any Yahoo Finance symbol — equities, ETFs, crypto.
          </p>
        </div>
      )}
    </div>
  );
}
