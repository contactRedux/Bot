/**
 * pages/StockChart.tsx — Yahoo-Finance-style stock price explorer.
 *
 * Features:
 *  - Editable watchlist (persisted to localStorage), defaulting to S&P 500
 *    major constituents.  Add tickers via the input box; remove with ×.
 *  - Time ranges: 1D / 1W / 1M / 3M / 1Y / ALL
 *  - Intraday/daily price + volume chart via recharts
 *  - Paper trade panel: BUY / SELL market or limit orders via POST /api/trading/order
 */
import { useState, useEffect } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
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
import { fetchPriceHistory, submitManualOrder } from "@/lib/api";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const WATCHLIST_KEY = "stockchart_watchlist";

const DEFAULT_WATCHLIST = [
  "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META",
  "TSLA", "SPY", "BTC-USD", "ETH-USD",
];

type Range = "1D" | "1W" | "1M" | "3M" | "1Y" | "ALL";

const RANGE_CONFIG: Record<Range, { interval: string; limit: number; label: string }> = {
  "1D":  { interval: "5m",  limit: 78,   label: "Today (5-min bars)"       },
  "1W":  { interval: "1h",  limit: 168,  label: "Past week (1-hr bars)"    },
  "1M":  { interval: "1d",  limit: 31,   label: "Past month (daily bars)"  },
  "3M":  { interval: "1d",  limit: 93,   label: "Past 3 months"            },
  "1Y":  { interval: "1d",  limit: 365,  label: "Past year (daily bars)"   },
  "ALL": { interval: "1d",  limit: 5000, label: "All available data"       },
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

function loadWatchlist(): string[] {
  try {
    const raw = localStorage.getItem(WATCHLIST_KEY);
    if (raw !== null) {
      const parsed = JSON.parse(raw) as string[];
      // Accept any array — including empty — so users can clear their watchlist.
      // Only fall back to the default when nothing has ever been saved.
      if (Array.isArray(parsed)) return parsed;
    }
  } catch { /* ignore */ }
  return DEFAULT_WATCHLIST;
}

function saveWatchlist(list: string[]) {
  try { localStorage.setItem(WATCHLIST_KEY, JSON.stringify(list)); } catch { /* ignore */ }
}

// ---------------------------------------------------------------------------
// Custom tooltip
// ---------------------------------------------------------------------------

const ChartTooltip = ({
  active, payload, label,
}: {
  active?: boolean;
  payload?: Array<{ name: string; value: number }>;
  label?: string;
}) => {
  if (!active || !payload?.length) return null;
  const price = payload.find((p) => p.name === "Close");
  const vol   = payload.find((p) => p.name === "Volume");
  return (
    <div className="rounded border border-zinc-600 bg-zinc-900 px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 font-mono text-zinc-400">{label}</p>
      {price && <p className="font-mono font-semibold text-sky-400">${formatPrice(price.value)}</p>}
      {vol && <p className="font-mono text-zinc-500">Vol: {vol.value.toLocaleString()}</p>}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Paper trade panel
// ---------------------------------------------------------------------------

function PaperTradePanel({ ticker, lastPrice }: { ticker: string; lastPrice: number }) {
  const [side, setSide]           = useState<"buy" | "sell">("buy");
  const [qty, setQty]             = useState("1");
  const [orderType, setOrderType] = useState<"market" | "limit">("market");
  const [limitPrice, setLimitPrice] = useState("");
  const [toast, setToast]         = useState<{ msg: string; ok: boolean } | null>(null);

  const mutation = useMutation({
    mutationFn: submitManualOrder,
    onSuccess: (data) => {
      if (data.success) {
        setToast({
          msg: `✓ Filled ${data.quantity} × ${data.ticker} @ $${formatPrice(data.fill_price)} (commission $${formatPrice(data.commission)})`,
          ok: true,
        });
      } else {
        setToast({ msg: `Order ${data.status}: no price available for ${data.ticker}.`, ok: false });
      }
      setTimeout(() => setToast(null), 5000);
    },
    onError: (e: Error) => {
      setToast({ msg: e.message, ok: false });
      setTimeout(() => setToast(null), 5000);
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const q = parseFloat(qty);
    if (!q || q <= 0) return;
    mutation.mutate({
      ticker,
      side,
      quantity: q,
      order_type: orderType,
      limit_price: orderType === "limit" && limitPrice ? parseFloat(limitPrice) : null,
    });
  };

  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-4">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-zinc-500">
        Paper Trade · <span className="font-mono text-zinc-300">{ticker}</span>
        {lastPrice > 0 && (
          <span className="ml-2 text-zinc-500">@ ${formatPrice(lastPrice)}</span>
        )}
      </h3>
      <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-3">
        {/* Side */}
        <div className="flex rounded border border-zinc-600 overflow-hidden text-xs font-semibold">
          {(["buy", "sell"] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setSide(s)}
              className={`px-4 py-1.5 transition-colors ${
                side === s
                  ? s === "buy"
                    ? "bg-emerald-500/20 text-emerald-400"
                    : "bg-rose-500/20 text-rose-400"
                  : "text-zinc-500 hover:text-zinc-100"
              }`}
            >
              {s.toUpperCase()}
            </button>
          ))}
        </div>

        {/* Quantity */}
        <div className="flex flex-col gap-1">
          <label className="text-xs text-zinc-500">Qty</label>
          <input
            type="number"
            min="0.0001"
            step="any"
            value={qty}
            onChange={(e) => setQty(e.target.value)}
            className="w-24 rounded border border-zinc-600 bg-zinc-900 px-2 py-1.5 font-mono text-sm text-zinc-100 focus:border-sky-400 focus:outline-none"
          />
        </div>

        {/* Order type */}
        <div className="flex flex-col gap-1">
          <label className="text-xs text-zinc-500">Type</label>
          <select
            value={orderType}
            onChange={(e) => setOrderType(e.target.value as "market" | "limit")}
            className="rounded border border-zinc-600 bg-zinc-900 px-2 py-1.5 text-sm text-zinc-100 focus:border-sky-400 focus:outline-none"
          >
            <option value="market">Market</option>
            <option value="limit">Limit</option>
          </select>
        </div>

        {/* Limit price (conditional) */}
        {orderType === "limit" && (
          <div className="flex flex-col gap-1">
            <label className="text-xs text-zinc-500">Limit $</label>
            <input
              type="number"
              min="0"
              step="any"
              value={limitPrice}
              onChange={(e) => setLimitPrice(e.target.value)}
              placeholder={lastPrice > 0 ? formatPrice(lastPrice) : "price"}
              className="w-28 rounded border border-zinc-600 bg-zinc-900 px-2 py-1.5 font-mono text-sm text-zinc-100 focus:border-sky-400 focus:outline-none"
            />
          </div>
        )}

        <button
          type="submit"
          disabled={mutation.isPending}
          className={`rounded px-4 py-2 text-xs font-semibold transition-colors ${
            side === "buy"
              ? "bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30"
              : "bg-rose-500/20 text-rose-400 hover:bg-rose-500/30"
          } disabled:opacity-40`}
        >
          {mutation.isPending ? "Submitting…" : `${side.toUpperCase()} ${ticker}`}
        </button>
      </form>

      {toast && (
        <p className={`mt-2 text-xs ${toast.ok ? "text-emerald-400" : "text-rose-400"}`}>
          {toast.msg}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function StockChart() {
  const [watchlist, setWatchlist]     = useState<string[]>(loadWatchlist);
  const [ticker, setTicker]           = useState<string>(() => loadWatchlist()[0] ?? "AAPL");
  const [addInput, setAddInput]       = useState("");
  const [range, setRange]             = useState<Range>("1Y");

  // Persist watchlist changes
  useEffect(() => { saveWatchlist(watchlist); }, [watchlist]);

  const { interval, limit } = RANGE_CONFIG[range];

  const { data, isLoading, isError } = useQuery({
    queryKey: ["stockChart", ticker, interval, limit],
    queryFn: () => fetchPriceHistory(ticker, interval, limit),
    refetchInterval: range === "1D" ? 60_000 : 300_000,
    staleTime: range === "1D" ? 30_000 : 120_000,
    enabled: !!ticker,
  });

  const points = data?.points ?? [];
  const chartData = points.map((p) => ({
    time: p.time, close: p.close, open: p.open,
    high: p.high, low: p.low, volume: p.volume ?? 0,
  }));

  const firstClose = chartData[0]?.close ?? 0;
  const lastClose  = chartData[chartData.length - 1]?.close ?? 0;
  const change     = pctChange(firstClose, lastClose);
  const changeAbs  = lastClose - firstClose;
  const isPositive = change >= 0;

  const tickFormatter = (val: string) => {
    if (range === "1D") return val.slice(11, 16);
    if (range === "1W") return val.slice(5, 16).replace("T", " ");
    return val.slice(0, 10);
  };

  const handleAddTicker = (e: React.FormEvent) => {
    e.preventDefault();
    const t = addInput.trim().toUpperCase();
    if (t && !watchlist.includes(t)) {
      const next = [...watchlist, t];
      setWatchlist(next);
    }
    if (t) setTicker(t);
    setAddInput("");
  };

  const handleRemoveTicker = (t: string) => {
    const next = watchlist.filter((w) => w !== t);
    setWatchlist(next.length > 0 ? next : DEFAULT_WATCHLIST);
    if (ticker === t) setTicker(next[0] ?? DEFAULT_WATCHLIST[0]);
  };

  return (
    <div className="space-y-5">
      {/* Page header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-zinc-100">Stock Chart</h1>
        {/* Add ticker to watchlist */}
        <form onSubmit={handleAddTicker} className="flex gap-2">
          <input
            value={addInput}
            onChange={(e) => setAddInput(e.target.value.toUpperCase())}
            placeholder="Add ticker…"
            className="w-32 rounded border border-zinc-600 bg-zinc-900 px-3 py-1.5 font-mono text-sm text-zinc-100 placeholder-zinc-600 focus:border-sky-400 focus:outline-none"
          />
          <button
            type="submit"
            className="rounded border border-zinc-600 px-3 py-1.5 text-xs text-zinc-300 hover:border-sky-400 hover:text-sky-400"
          >
            Add
          </button>
        </form>
      </div>

      {/* Watchlist pills */}
      <div className="flex flex-wrap gap-1.5">
        {watchlist.map((t) => (
          <div
            key={t}
            className={`flex items-center gap-1 rounded border pl-2 pr-1 py-0.5 font-mono text-xs font-medium transition-colors ${
              ticker === t
                ? "border-sky-400/50 bg-sky-500/15 text-sky-400"
                : "border-zinc-600 text-zinc-400"
            }`}
          >
            <button onClick={() => setTicker(t)} className="hover:text-zinc-100">
              {t}
            </button>
            <button
              onClick={() => handleRemoveTicker(t)}
              className="ml-0.5 rounded px-0.5 text-zinc-600 hover:text-rose-400"
              title={`Remove ${t}`}
            >
              ×
            </button>
          </div>
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
                  range === r ? "bg-sky-500 text-zinc-900" : "text-zinc-400 hover:text-zinc-100"
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
            <p className="text-xs">Try the 1Y or ALL range, or check that the ticker symbol is valid.</p>
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
                tickFormatter={(v: number) => `$${v >= 1000 ? `${(v / 1000).toFixed(1)}k` : formatPrice(v)}`}
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
              {firstClose > 0 && (
                <ReferenceLine yAxisId="price" y={firstClose} stroke="#52525b" strokeDasharray="4 3" />
              )}
              <Bar
                yAxisId="vol"
                dataKey="volume"
                name="Volume"
                fill={isPositive ? "#34d39930" : "#fb718530"}
                radius={[2, 2, 0, 0]}
              />
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

      {/* Paper trade panel */}
      <PaperTradePanel ticker={ticker} lastPrice={lastClose} />
    </div>
  );
}
