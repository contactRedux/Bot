/**
 * pages/StockChart.tsx — Yahoo-Finance-style stock price explorer.
 *
 * Lets the user pick any ticker from the watched universe (or type a custom
 * one) and select a time range (1D / 1W / 1M / 3M / 1Y / ALL).  Renders a
 * full-width price + volume chart using the existing /api/portfolio/price-history
 * endpoint, which reads from the DataStore (bars collected by the DataPipeline).
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ComposedChart,
  Area,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { fetchPriceHistory } from "@/lib/api";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_TICKERS = [
  "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META", "SPY",
  "BTC-USD", "ETH-USD",
];

type Range = "1D" | "1W" | "1M" | "3M" | "1Y" | "ALL";

const RANGE_CONFIG: Record<Range, { interval: string; limit: number; label: string }> = {
  "1D":  { interval: "5m",  limit: 78,   label: "Today (5-min bars)"       },
  "1W":  { interval: "1h",  limit: 168,  label: "Past week (1-hr bars)"    },
  "1M":  { interval: "1d",  limit: 31,   label: "Past month (daily bars)"  },
  "3M":  { interval: "1d",  limit: 93,   label: "Past 3 months"            },
  "1Y":  { interval: "1d",  limit: 365,  label: "Past year (daily bars)"   },
  "ALL": { interval: "1d",  limit: 2000, label: "All available data"       },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function pctChange(first: number, last: number): number {
  if (!first) return 0;
  return ((last - first) / first) * 100;
}

function formatPrice(v: number): string {
  return v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// ---------------------------------------------------------------------------
// Custom tooltip
// ---------------------------------------------------------------------------

const ChartTooltip = ({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ name: string; value: number; color: string }>;
  label?: string;
}) => {
  if (!active || !payload?.length) return null;
  const price = payload.find((p) => p.name === "Close");
  const vol   = payload.find((p) => p.name === "Volume");
  return (
    <div className="rounded border border-zinc-600 bg-zinc-900 px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 font-mono text-zinc-400">{label}</p>
      {price && (
        <p className="font-mono font-semibold text-sky-400">
          ${formatPrice(price.value)}
        </p>
      )}
      {vol && (
        <p className="font-mono text-zinc-500">
          Vol: {vol.value.toLocaleString()}
        </p>
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function StockChart() {
  const [ticker, setTicker]       = useState<string>("AAPL");
  const [customInput, setCustomInput] = useState<string>("");
  const [range, setRange]         = useState<Range>("1Y");

  const { interval, limit } = RANGE_CONFIG[range];

  const { data, isLoading, isError } = useQuery({
    queryKey: ["stockChart", ticker, interval, limit],
    queryFn: () => fetchPriceHistory(ticker, interval, limit),
    refetchInterval: range === "1D" ? 60_000 : 300_000, // refresh faster intraday
    staleTime: range === "1D" ? 30_000 : 120_000,
    enabled: !!ticker,
  });

  const points = data?.points ?? [];

  // Compute chart data with open-period color (green/red)
  const chartData = points.map((p) => ({
    time: p.time,
    close: p.close,
    open: p.open,
    high: p.high,
    low: p.low,
    volume: p.volume ?? 0,
  }));

  const firstClose = chartData[0]?.close ?? 0;
  const lastClose  = chartData[chartData.length - 1]?.close ?? 0;
  const change     = pctChange(firstClose, lastClose);
  const changeAbs  = lastClose - firstClose;
  const isPositive = change >= 0;

  // Tick formatter — shorten date labels based on range
  const tickFormatter = (val: string) => {
    if (range === "1D") return val.slice(11, 16);                    // HH:MM
    if (range === "1W") return val.slice(5, 16).replace("T", " ");  // MM-DD HH:MM
    return val.slice(0, 10);                                          // YYYY-MM-DD
  };

  const handleCustomSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const t = customInput.trim().toUpperCase();
    if (t) { setTicker(t); setCustomInput(""); }
  };

  return (
    <div className="space-y-5">
      {/* Page header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-zinc-100">Stock Chart</h1>
        {/* Custom ticker input */}
        <form onSubmit={handleCustomSubmit} className="flex gap-2">
          <input
            value={customInput}
            onChange={(e) => setCustomInput(e.target.value.toUpperCase())}
            placeholder="Type ticker…"
            className="w-32 rounded border border-zinc-600 bg-zinc-900 px-3 py-1.5 font-mono text-sm text-zinc-100 placeholder-zinc-600 focus:border-sky-400 focus:outline-none"
          />
          <button
            type="submit"
            className="rounded border border-zinc-600 px-3 py-1.5 text-xs text-zinc-300 hover:border-sky-400 hover:text-sky-400"
          >
            Go
          </button>
        </form>
      </div>

      {/* Ticker selector pills */}
      <div className="flex flex-wrap gap-2">
        {DEFAULT_TICKERS.map((t) => (
          <button
            key={t}
            onClick={() => setTicker(t)}
            className={`rounded border px-3 py-1 font-mono text-xs font-medium transition-colors ${
              ticker === t
                ? "border-sky-400/50 bg-sky-500/15 text-sky-400"
                : "border-zinc-600 text-zinc-400 hover:border-zinc-400 hover:text-zinc-100"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Chart card */}
      <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-4">
        {/* Header row */}
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="font-mono text-2xl font-bold text-zinc-100">{ticker}</p>
            {lastClose > 0 && (
              <div className="flex items-baseline gap-2">
                <span className="font-mono text-xl text-zinc-100">${formatPrice(lastClose)}</span>
                <span className={`font-mono text-sm font-semibold ${isPositive ? "text-emerald-400" : "text-rose-400"}`}>
                  {isPositive ? "+" : ""}{formatPrice(changeAbs)} ({isPositive ? "+" : ""}{change.toFixed(2)}%)
                </span>
                <span className="text-xs text-zinc-500">{RANGE_CONFIG[range].label}</span>
              </div>
            )}
          </div>

          {/* Range buttons */}
          <div className="flex gap-1">
            {(Object.keys(RANGE_CONFIG) as Range[]).map((r) => (
              <button
                key={r}
                onClick={() => setRange(r)}
                className={`rounded px-3 py-1 text-xs font-semibold transition-colors ${
                  range === r
                    ? "bg-sky-500 text-zinc-900"
                    : "text-zinc-400 hover:text-zinc-100"
                }`}
              >
                {r}
              </button>
            ))}
          </div>
        </div>

        {/* Chart */}
        {isLoading ? (
          <div className="flex h-72 items-center justify-center text-sm text-zinc-500">
            Loading price data…
          </div>
        ) : isError ? (
          <div className="flex h-72 items-center justify-center text-sm text-rose-400">
            Failed to load data. Make sure the API is running and data has been collected.
          </div>
        ) : chartData.length === 0 ? (
          <div className="flex h-72 flex-col items-center justify-center gap-2 text-sm text-zinc-500">
            <p>No price data for <span className="font-mono text-zinc-300">{ticker}</span> at {interval} interval.</p>
            <p className="text-xs">The DataPipeline collects daily bars automatically. Try the 1Y range or run a backtest first to populate the database.</p>
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={340}>
            <ComposedChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="priceGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor={isPositive ? "#34d399" : "#fb7185"} stopOpacity={0.2} />
                  <stop offset="95%" stopColor={isPositive ? "#34d399" : "#fb7185"} stopOpacity={0}   />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#3f3f46" vertical={false} />
              <XAxis
                dataKey="time"
                tick={{ fill: "#a1a1aa", fontSize: 10 }}
                tickLine={false}
                minTickGap={70}
                tickFormatter={tickFormatter}
              />
              <YAxis
                yAxisId="price"
                orientation="right"
                domain={["auto", "auto"]}
                tick={{ fill: "#a1a1aa", fontSize: 10 }}
                tickLine={false}
                width={64}
                tickFormatter={(v: number) => `$${v >= 1000 ? `${(v/1000).toFixed(1)}k` : formatPrice(v)}`}
              />
              <YAxis
                yAxisId="vol"
                orientation="left"
                domain={[0, (max: number) => max * 4]}
                tick={false}
                tickLine={false}
                width={0}
                axisLine={false}
              />
              <Tooltip content={<ChartTooltip />} />
              {/* Opening price reference line */}
              {firstClose > 0 && (
                <ReferenceLine yAxisId="price" y={firstClose} stroke="#52525b" strokeDasharray="4 3" />
              )}
              {/* Volume bars behind the price line */}
              <Bar
                yAxisId="vol"
                dataKey="volume"
                name="Volume"
                fill={isPositive ? "#34d39930" : "#fb718530"}
                radius={[2, 2, 0, 0]}
              />
              {/* Price area */}
              <Area
                yAxisId="price"
                type="monotone"
                dataKey="close"
                name="Close"
                stroke={isPositive ? "#34d399" : "#fb7185"}
                strokeWidth={1.5}
                fill="url(#priceGrad)"
                dot={false}
                activeDot={{ r: 4, fill: isPositive ? "#34d399" : "#fb7185" }}
              />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
