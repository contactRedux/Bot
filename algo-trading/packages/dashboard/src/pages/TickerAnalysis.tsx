/**
 * pages/TickerAnalysis.tsx — Composite technical analysis — Apple light aesthetic.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchAnalysis } from "@/lib/api";
import type { AnalysisResponse, AnalystConsensus } from "@/lib/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function ratingColor(r: AnalysisResponse["rating"]) {
  switch (r) {
    case "Strong Buy":  return "text-[#30d158] bg-[#30d158]/10 border-[#30d158]/30";
    case "Buy":         return "text-[#30d158] bg-[#30d158]/10 border-[#30d158]/20";
    case "Hold":        return "text-[#6e6e73] bg-[#f5f5f7] border-[#e5e5ea]";
    case "Sell":        return "text-[#ff3b30] bg-[#ff3b30]/10 border-[#ff3b30]/20";
    case "Strong Sell": return "text-[#ff3b30] bg-[#ff3b30]/10 border-[#ff3b30]/30";
  }
}

function scoreColor(score: number) {
  if (score >= 0.5)  return "bg-[#30d158]";
  if (score >= 0.1)  return "bg-[#30d158]/70";
  if (score <= -0.5) return "bg-[#ff3b30]";
  if (score <= -0.1) return "bg-[#ff3b30]/70";
  return "bg-[#c7c7cc]";
}

function fmt(n: number, decimals = 2) {
  return n.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function pctBadge(v: number) {
  return (
    <span className={v >= 0 ? "text-[#30d158]" : "text-[#ff3b30]"}>
      {v >= 0 ? "+" : ""}{fmt(v)}%
    </span>
  );
}

// ---------------------------------------------------------------------------
// Signal bar
// ---------------------------------------------------------------------------
function SignalBar({ label, score }: { label: string; score: number }) {
  const pct = Math.abs(score) * 50;
  const isPositive = score >= 0;
  return (
    <div className="flex items-center gap-3">
      <span className="w-28 shrink-0 text-xs text-[#6e6e73]">{label}</span>
      <div className="relative flex h-4 flex-1 items-center rounded bg-[#f5f5f7]">
        <div className="absolute left-1/2 h-full w-px bg-[#e5e5ea]" />
        <div
          className={`absolute h-3 rounded ${scoreColor(score)}`}
          style={{
            width: `${pct}%`,
            [isPositive ? "left" : "right"]: "50%",
          }}
        />
      </div>
      <span className={`w-10 text-right text-xs ${score >= 0 ? "text-[#30d158]" : "text-[#ff3b30]"}`}>
        {score >= 0 ? "+" : ""}{fmt(score, 2)}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Analyst consensus panel
// ---------------------------------------------------------------------------
function AnalystConsensusPanel({ consensus }: { consensus: AnalystConsensus | null }) {
  if (!consensus || consensus.total_analysts === 0) {
    return (
      <div className="rounded-xl border border-[#e5e5ea] bg-white p-4">
        <div className="mb-2 text-xs font-medium uppercase tracking-wider text-[#6e6e73]">
          Wall St. Analyst Consensus
        </div>
        <p className="text-sm text-[#8e8e93]">No analyst data available (equity-only via yfinance).</p>
      </div>
    );
  }

  const total = consensus.total_analysts;
  const bars: Array<{ label: string; count: number; color: string }> = [
    { label: "Strong Buy", count: consensus.strong_buy,   color: "bg-[#30d158]" },
    { label: "Buy",        count: consensus.buy,          color: "bg-[#30d158]/70" },
    { label: "Hold",       count: consensus.hold,         color: "bg-[#c7c7cc]" },
    { label: "Sell",       count: consensus.sell,         color: "bg-[#ff3b30]/70" },
    { label: "Strong Sell",count: consensus.strong_sell,  color: "bg-[#ff3b30]" },
  ];

  const consensusRatingColor = (r: string | null) => {
    if (!r) return "text-[#6e6e73] border-[#e5e5ea]";
    if (r === "Strong Buy")  return "text-[#30d158] border-[#30d158]/30 bg-[#30d158]/10";
    if (r === "Buy")         return "text-[#30d158] border-[#30d158]/20 bg-[#30d158]/10";
    if (r === "Hold")        return "text-[#6e6e73] border-[#e5e5ea] bg-[#f5f5f7]";
    if (r === "Sell")        return "text-[#ff3b30] border-[#ff3b30]/20 bg-[#ff3b30]/10";
    return "text-[#ff3b30] border-[#ff3b30]/30 bg-[#ff3b30]/10";
  };

  return (
    <div className="rounded-xl border border-[#e5e5ea] bg-white p-4">
      <div className="mb-3 text-xs font-medium uppercase tracking-wider text-[#6e6e73]">
        Wall St. Analyst Consensus · {total} analysts
      </div>

      <div className="flex flex-wrap items-center gap-4 mb-4">
        {consensus.consensus_rating && (
          <span className={`rounded-lg border px-4 py-2 text-lg font-bold ${consensusRatingColor(consensus.consensus_rating)}`}>
            {consensus.consensus_rating}
          </span>
        )}
        {consensus.consensus_score !== null && (
          <div className="text-sm text-[#6e6e73]">
            Score <span className="font-mono text-[#1d1d1f]">{consensus.consensus_score.toFixed(2)}</span>
            <span className="text-[#8e8e93]"> / 5.0</span>
          </div>
        )}
      </div>

      <div className="mb-3">
        <div className="flex h-4 w-full overflow-hidden rounded-full">
          {bars.map(({ label, count, color }) =>
            count > 0 ? (
              <div
                key={label}
                className={`${color} transition-all`}
                style={{ width: `${(count / total) * 100}%` }}
                title={`${label}: ${count}`}
              />
            ) : null
          )}
        </div>
        <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
          {bars.filter(b => b.count > 0).map(({ label, count, color }) => (
            <span key={label} className="flex items-center gap-1 text-xs text-[#6e6e73]">
              <span className={`inline-block h-2 w-2 rounded-full ${color}`} />
              {label}: {count} ({((count / total) * 100).toFixed(0)}%)
            </span>
          ))}
        </div>
      </div>

      {consensus.target_price_avg !== null && (
        <div className="mt-3 grid grid-cols-3 gap-2 rounded-lg border border-[#e5e5ea] bg-[#f5f5f7] p-2 text-xs">
          <div className="text-center">
            <div className="text-[#6e6e73]">Low Target</div>
            <div className="font-mono text-[#1d1d1f]">
              {consensus.target_price_low !== null ? `$${consensus.target_price_low.toFixed(2)}` : "—"}
            </div>
          </div>
          <div className="text-center">
            <div className="text-[#6e6e73]">Avg Target</div>
            <div className="font-mono font-semibold text-[#007aff]">
              ${consensus.target_price_avg.toFixed(2)}
            </div>
          </div>
          <div className="text-center">
            <div className="text-[#6e6e73]">High Target</div>
            <div className="font-mono text-[#1d1d1f]">
              {consensus.target_price_high !== null ? `$${consensus.target_price_high.toFixed(2)}` : "—"}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// RSI gauge
// ---------------------------------------------------------------------------
function RsiGauge({ rsi }: { rsi: number }) {
  const color =
    rsi < 30 ? "text-[#30d158]"
    : rsi > 70 ? "text-[#ff3b30]"
    : "text-[#1d1d1f]";
  const label = rsi < 30 ? "Oversold" : rsi > 70 ? "Overbought" : "Neutral";
  const barPct = Math.min(100, Math.max(0, rsi));
  const barColor = rsi < 30 ? "bg-[#30d158]" : rsi > 70 ? "bg-[#ff3b30]" : "bg-[#007aff]";
  return (
    <div className="flex flex-col gap-1">
      <div className="flex justify-between text-xs text-[#6e6e73]">
        <span>0</span><span>50</span><span>100</span>
      </div>
      <div className="relative h-3 rounded-full bg-[#f5f5f7]">
        <div className={`absolute left-0 h-3 rounded-full ${barColor}`} style={{ width: `${barPct}%` }} />
        <div className="absolute left-[30%] h-3 w-px bg-[#30d158]/40" />
        <div className="absolute left-[70%] h-3 w-px bg-[#ff3b30]/40" />
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
        <h1 className="text-xl font-semibold text-[#1d1d1f]">Ticker Analysis</h1>
        <p className="text-sm text-[#6e6e73]">
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
          className="flex-1 rounded-xl border border-[#e5e5ea] bg-[#f5f5f7] px-4 py-2.5 text-sm text-[#1d1d1f] placeholder-[#8e8e93] focus:border-[#007aff] focus:outline-none"
        />
        <button
          type="submit"
          disabled={isFetching}
          className="rounded-xl bg-[#007aff] px-5 py-2.5 text-sm font-medium text-white hover:bg-[#007aff]/90 disabled:opacity-50"
        >
          {isFetching ? "Analysing…" : "Analyse"}
        </button>
      </form>

      {/* Error */}
      {error && (
        <div className="rounded-xl border border-[#ff3b30]/20 bg-[#ff3b30]/5 px-4 py-3 text-sm text-[#ff3b30]">
          {(error as Error).message}
        </div>
      )}

      {/* Results */}
      {data && (
        <div className="grid gap-4 lg:grid-cols-3">

          {/* Rating card */}
          <div className="rounded-xl border border-[#e5e5ea] bg-white p-5 lg:col-span-1">
            <div className="mb-1 text-xs font-medium uppercase tracking-wider text-[#6e6e73]">
              {data.ticker} · Composite Rating
            </div>
            <div className={`mt-3 inline-block rounded-xl border px-5 py-3 text-2xl font-bold ${ratingColor(data.rating)}`}>
              {data.rating}
            </div>
            <div className="mt-4 space-y-1 text-sm">
              <div className="flex justify-between">
                <span className="text-[#6e6e73]">Composite score</span>
                <span className={data.composite_score >= 0 ? "text-[#30d158]" : "text-[#ff3b30]"}>
                  {data.composite_score >= 0 ? "+" : ""}{fmt(data.composite_score, 3)}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-[#6e6e73]">Confidence</span>
                <span className="text-[#1d1d1f]">{fmt(data.confidence_pct, 1)}%</span>
              </div>
              <div className="flex justify-between">
                <span className="text-[#6e6e73]">Last price</span>
                <span className="text-[#1d1d1f]">${fmt(data.price_stats.last_price, 2)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-[#6e6e73]">1-day change</span>
                {pctBadge(data.price_stats.pct_change_1d)}
              </div>
              <div className="flex justify-between">
                <span className="text-[#6e6e73]">1-month change</span>
                {pctBadge(data.price_stats.pct_change_1m)}
              </div>
              <div className="flex justify-between">
                <span className="text-[#6e6e73]">Bars used</span>
                <span className="text-[#6e6e73]">{data.bar_count} daily</span>
              </div>
            </div>
          </div>

          {/* Indicators + Signals */}
          <div className="space-y-4 lg:col-span-2">

            {/* RSI */}
            <div className="rounded-xl border border-[#e5e5ea] bg-white p-4">
              <div className="mb-3 text-xs font-medium uppercase tracking-wider text-[#6e6e73]">RSI (14)</div>
              <RsiGauge rsi={data.indicators.rsi} />
            </div>

            {/* Signal scores */}
            <div className="rounded-xl border border-[#e5e5ea] bg-white p-4">
              <div className="mb-3 text-xs font-medium uppercase tracking-wider text-[#6e6e73]">Signal Scores (10 sources)</div>
              <div className="space-y-2">
                <SignalBar label="MACD"         score={data.signal_scores.macd} />
                <SignalBar label="MA Trend"     score={data.signal_scores.ma_trend} />
                <SignalBar label="RSI"          score={data.signal_scores.rsi} />
                <SignalBar label="Bollinger"    score={data.signal_scores.bollinger} />
                <SignalBar label="Short Trend"  score={data.signal_scores.short_trend} />
                <SignalBar label="Stochastic"   score={data.signal_scores.stochastic ?? 0} />
                <SignalBar label="Williams %R"  score={data.signal_scores.williams_r ?? 0} />
                <SignalBar label="CCI"          score={data.signal_scores.cci ?? 0} />
                <SignalBar label="EMA Cross"    score={data.signal_scores.ema_cross ?? 0} />
                <SignalBar label="VWAP"         score={data.signal_scores.vwap ?? 0} />
              </div>
            </div>
          </div>

          {/* Analyst consensus */}
          <div className="lg:col-span-3">
            <AnalystConsensusPanel consensus={data.analyst_consensus ?? null} />
          </div>

          {/* Indicators grid */}
          <div className="rounded-xl border border-[#e5e5ea] bg-white p-4 lg:col-span-2">
            <div className="mb-3 text-xs font-medium uppercase tracking-wider text-[#6e6e73]">Technical Indicators</div>
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
                ["EMA 9",        `$${fmt(data.indicators.ema_9 ?? 0)}`],
                ["EMA 21",       `$${fmt(data.indicators.ema_21 ?? 0)}`],
                ["Stoch %K",     `${fmt(data.indicators.stoch_k ?? 50, 1)}`],
                ["Stoch %D",     `${fmt(data.indicators.stoch_d ?? 50, 1)}`],
                ["Williams %R",  `${fmt(data.indicators.williams_r ?? -50, 1)}`],
                ["CCI",          `${fmt(data.indicators.cci ?? 0, 1)}`],
                ["VWAP 20",      `$${fmt(data.indicators.vwap_20 ?? 0)}`],
                ["Vol Ratio",    `${fmt(data.indicators.volume_ratio ?? 1, 2)}×`],
                ["ATR",          fmt(data.indicators.atr, 4)],
              ].map(([label, val]) => (
                <div key={label} className="flex justify-between border-b border-[#e5e5ea] py-1.5">
                  <span className="text-[#6e6e73]">{label}</span>
                  <span className="text-[#1d1d1f]">{val}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Reasoning */}
          <div className="rounded-xl border border-[#e5e5ea] bg-white p-4 lg:col-span-1">
            <div className="mb-3 text-xs font-medium uppercase tracking-wider text-[#6e6e73]">Reasoning</div>
            <ul className="space-y-2">
              {data.reasoning.map((r, i) => (
                <li key={i} className="flex gap-2 text-sm text-[#3a3a3c]">
                  <span className="mt-0.5 shrink-0 text-[#007aff]">›</span>
                  {r}
                </li>
              ))}
            </ul>
            <p className="mt-4 text-xs text-[#8e8e93]">
              As of {new Date(data.as_of).toLocaleString()}. For informational purposes only.
            </p>
          </div>
        </div>
      )}

      {/* Empty state */}
      {!data && !isFetching && !error && (
        <div className="rounded-xl border border-[#e5e5ea] bg-[#f5f5f7] py-20 text-center">
          <p className="text-sm text-[#6e6e73]">
            Enter a ticker above to see a composite technical analysis.
          </p>
          <p className="mt-1 text-xs text-[#8e8e93]">
            Works for any Yahoo Finance symbol — equities, ETFs, crypto.
          </p>
        </div>
      )}
    </div>
  );
}
