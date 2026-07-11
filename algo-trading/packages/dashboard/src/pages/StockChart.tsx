/**
 * pages/StockChart.tsx — Apple-style stock price explorer.
 *
 * Features:
 *  - Editable watchlist (persisted to localStorage)
 *  - Time ranges: 1D / 1W / 1M / 3M / 1Y / ALL  (pill buttons)
 *  - Gradient area chart + volume bars via recharts
 *  - Technical indicator overlays (SMA, EMA, Bollinger)
 *  - Paper trade panel: BUY / SELL orders via POST /api/trading/order
 */
import { useState, useEffect } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import {
  ComposedChart,
  Area,
  Bar,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { fetchPriceHistory, submitManualOrder } from "@/lib/api";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const WATCHLIST_KEY = "stockchart_watchlist_v2";
const DEFAULT_WATCHLIST = ["VOO"];

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
      if (Array.isArray(parsed)) return parsed;
    }
  } catch { /* ignore */ }
  return DEFAULT_WATCHLIST;
}

function saveWatchlist(list: string[]) {
  try { localStorage.setItem(WATCHLIST_KEY, JSON.stringify(list)); } catch { /* ignore */ }
}

// ---------------------------------------------------------------------------
// Technical indicator helpers
// ---------------------------------------------------------------------------

function computeSma(closes: number[], period: number): (number | null)[] {
  return closes.map((_, i) => {
    if (i < period - 1) return null;
    return closes.slice(i - period + 1, i + 1).reduce((a, b) => a + b, 0) / period;
  });
}

function computeEma(closes: number[], period: number): (number | null)[] {
  const k = 2 / (period + 1);
  const result: (number | null)[] = [];
  let prev: number | null = null;
  for (let i = 0; i < closes.length; i++) {
    if (i < period - 1) { result.push(null); continue; }
    if (prev === null) {
      prev = closes.slice(0, period).reduce((a, b) => a + b, 0) / period;
      result.push(prev); continue;
    }
    prev = closes[i] * k + prev * (1 - k);
    result.push(prev);
  }
  return result;
}

function computeBollinger(closes: number[], period = 20): {
  upper: (number | null)[]; mid: (number | null)[]; lower: (number | null)[];
} {
  const upper: (number | null)[] = [];
  const mid: (number | null)[] = [];
  const lower: (number | null)[] = [];
  for (let i = 0; i < closes.length; i++) {
    if (i < period - 1) { upper.push(null); mid.push(null); lower.push(null); continue; }
    const window = closes.slice(i - period + 1, i + 1);
    const m = window.reduce((a, b) => a + b, 0) / period;
    const std = Math.sqrt(window.reduce((a, b) => a + (b - m) ** 2, 0) / period);
    mid.push(m);
    upper.push(m + 2 * std);
    lower.push(m - 2 * std);
  }
  return { upper, mid, lower };
}

type OverlayKey = "sma20" | "sma50" | "sma200" | "ema9" | "ema21" | "bb";

const OVERLAY_LABELS: Record<OverlayKey, string> = {
  sma20:  "SMA 20",
  sma50:  "SMA 50",
  sma200: "SMA 200",
  ema9:   "EMA 9",
  ema21:  "EMA 21",
  bb:     "BB Bands",
};

const OVERLAY_COLORS: Record<OverlayKey, string> = {
  sma20:  "#f59e0b",
  sma50:  "#007aff",
  sma200: "#a78bfa",
  ema9:   "#fb923c",
  ema21:  "#34d399",
  bb:     "#94a3b8",
};

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
    <div className="rounded-lg border border-[#e5e5ea] bg-white px-3 py-2 text-xs shadow-sm">
      <p className="mb-1 font-mono text-[#6e6e73]">{label}</p>
      {price && <p className="font-mono font-semibold text-[#1d1d1f]">${formatPrice(price.value)}</p>}
      {vol && <p className="font-mono text-[#6e6e73]">Vol: {vol.value.toLocaleString()}</p>}
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
    <div className="rounded-xl border border-[#e5e5ea] bg-white p-4">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-[#6e6e73]">
        Paper Trade · <span className="font-mono text-[#1d1d1f]">{ticker}</span>
        {lastPrice > 0 && (
          <span className="ml-2 text-[#6e6e73]">@ ${formatPrice(lastPrice)}</span>
        )}
      </h3>
      <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-3">
        {/* Side */}
        <div className="flex overflow-hidden rounded-lg border border-[#e5e5ea] text-xs font-semibold">
          {(["buy", "sell"] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setSide(s)}
              className={`px-4 py-1.5 transition-colors ${
                side === s
                  ? s === "buy"
                    ? "bg-[#30d158]/10 text-[#30d158]"
                    : "bg-[#ff3b30]/10 text-[#ff3b30]"
                  : "text-[#6e6e73] hover:text-[#1d1d1f]"
              }`}
            >
              {s.toUpperCase()}
            </button>
          ))}
        </div>

        {/* Quantity */}
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[#6e6e73]">Qty</label>
          <input
            type="number"
            min="0.0001"
            step="any"
            value={qty}
            onChange={(e) => setQty(e.target.value)}
            className="w-24 rounded-lg border border-[#e5e5ea] bg-[#f5f5f7] px-2 py-1.5 font-mono text-sm text-[#1d1d1f] focus:border-[#007aff] focus:outline-none"
          />
        </div>

        {/* Order type */}
        <div className="flex flex-col gap-1">
          <label className="text-xs text-[#6e6e73]">Type</label>
          <select
            value={orderType}
            onChange={(e) => setOrderType(e.target.value as "market" | "limit")}
            className="rounded-lg border border-[#e5e5ea] bg-[#f5f5f7] px-2 py-1.5 text-sm text-[#1d1d1f] focus:border-[#007aff] focus:outline-none"
          >
            <option value="market">Market</option>
            <option value="limit">Limit</option>
          </select>
        </div>

        {/* Limit price (conditional) */}
        {orderType === "limit" && (
          <div className="flex flex-col gap-1">
            <label className="text-xs text-[#6e6e73]">Limit $</label>
            <input
              type="number"
              min="0"
              step="any"
              value={limitPrice}
              onChange={(e) => setLimitPrice(e.target.value)}
              placeholder={lastPrice > 0 ? formatPrice(lastPrice) : "price"}
              className="w-28 rounded-lg border border-[#e5e5ea] bg-[#f5f5f7] px-2 py-1.5 font-mono text-sm text-[#1d1d1f] focus:border-[#007aff] focus:outline-none"
            />
          </div>
        )}

        <button
          type="submit"
          disabled={mutation.isPending}
          className={`rounded-lg px-4 py-2 text-xs font-semibold transition-colors ${
            side === "buy"
              ? "bg-[#30d158]/10 text-[#30d158] hover:bg-[#30d158]/20"
              : "bg-[#ff3b30]/10 text-[#ff3b30] hover:bg-[#ff3b30]/20"
          } disabled:opacity-40`}
        >
          {mutation.isPending ? "Submitting…" : `${side.toUpperCase()} ${ticker}`}
        </button>
      </form>

      {toast && (
        <p className={`mt-2 text-xs ${toast.ok ? "text-[#30d158]" : "text-[#ff3b30]"}`}>
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
  const [watchlist, setWatchlist]     = useState<string[]>(() => {
    const saved = loadWatchlist();
    return saved;
  });
  const [ticker, setTicker]           = useState<string>(() => loadWatchlist()[0] ?? "");
  const [addInput, setAddInput]       = useState("");
  const [range, setRange]             = useState<Range>("1Y");
  const [overlays, setOverlays]       = useState<Set<OverlayKey>>(new Set(["sma20", "sma50"]));

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
  const closes = points.map((p) => p.close);

  const sma20Arr  = overlays.has("sma20")  ? computeSma(closes, 20)  : [];
  const sma50Arr  = overlays.has("sma50")  ? computeSma(closes, 50)  : [];
  const sma200Arr = overlays.has("sma200") ? computeSma(closes, 200) : [];
  const ema9Arr   = overlays.has("ema9")   ? computeEma(closes, 9)   : [];
  const ema21Arr  = overlays.has("ema21")  ? computeEma(closes, 21)  : [];
  const bbData    = overlays.has("bb")     ? computeBollinger(closes) : null;

  const chartData = points.map((p, i) => ({
    time: p.time, close: p.close, open: p.open,
    high: p.high, low: p.low, volume: p.volume ?? 0,
    sma20:   sma20Arr[i]  ?? undefined,
    sma50:   sma50Arr[i]  ?? undefined,
    sma200:  sma200Arr[i] ?? undefined,
    ema9:    ema9Arr[i]   ?? undefined,
    ema21:   ema21Arr[i]  ?? undefined,
    bbUpper: bbData?.upper[i] ?? undefined,
    bbMid:   bbData?.mid[i]   ?? undefined,
    bbLower: bbData?.lower[i] ?? undefined,
  }));

  const firstClose = chartData[0]?.close ?? 0;
  const lastClose  = chartData[chartData.length - 1]?.close ?? 0;
  const change     = pctChange(firstClose, lastClose);
  const changeAbs  = lastClose - firstClose;
  const isPositive = change >= 0;
  const chartColor = isPositive ? "#30d158" : "#ff3b30";

  const toggleOverlay = (key: OverlayKey) => {
    setOverlays((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

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
    setWatchlist(next);
    if (ticker === t) setTicker(next[0] ?? "");
  };

  const handleResetWatchlist = () => {
    setWatchlist(DEFAULT_WATCHLIST);
    setTicker(DEFAULT_WATCHLIST[0]);
  };

  return (
    <div className="space-y-5">
      {/* Page header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-[#1d1d1f]">Stock Chart</h1>
        <div className="flex gap-2">
          <button
            onClick={handleResetWatchlist}
            className="rounded-lg border border-[#e5e5ea] px-3 py-1.5 text-xs text-[#8e8e93] hover:border-[#007aff] hover:text-[#007aff]"
            title="Reset to default watchlist (VOO)"
          >
            Reset
          </button>
          <form onSubmit={handleAddTicker} className="flex gap-2">
            <input
              value={addInput}
              onChange={(e) => setAddInput(e.target.value.toUpperCase())}
              placeholder="Add ticker…"
              className="w-32 rounded-lg border border-[#e5e5ea] bg-[#f5f5f7] px-3 py-1.5 font-mono text-sm text-[#1d1d1f] placeholder-[#8e8e93] focus:border-[#007aff] focus:outline-none"
            />
            <button
              type="submit"
              className="rounded-lg border border-[#e5e5ea] px-3 py-1.5 text-xs text-[#6e6e73] hover:border-[#007aff] hover:text-[#007aff]"
            >
              Add
            </button>
          </form>
        </div>
      </div>

      {/* Watchlist pills */}
      <div className="flex flex-wrap gap-1.5">
        {watchlist.length === 0 && (
          <p className="text-xs italic text-[#8e8e93]">No tickers — add one above.</p>
        )}
        {watchlist.map((t) => (
          <div
            key={t}
            className={`flex items-center gap-1 rounded-full border pl-3 pr-1.5 py-0.5 font-mono text-xs font-medium transition-colors ${
              ticker === t
                ? "border-[#007aff]/40 bg-[#007aff]/10 text-[#007aff]"
                : "border-[#e5e5ea] bg-[#f5f5f7] text-[#6e6e73]"
            }`}
          >
            <button onClick={() => setTicker(t)} className="hover:text-[#1d1d1f]">
              {t}
            </button>
            <button
              onClick={() => handleRemoveTicker(t)}
              className="ml-0.5 rounded-full px-0.5 text-[#8e8e93] hover:text-[#ff3b30]"
              title={`Remove ${t}`}
            >
              ×
            </button>
          </div>
        ))}
      </div>

      {/* Empty state */}
      {!ticker && (
        <div className="rounded-xl border border-[#e5e5ea] bg-[#f5f5f7] py-20 text-center">
          <p className="text-sm text-[#6e6e73]">Add a ticker above to view its chart.</p>
        </div>
      )}

      {/* Chart card */}
      {ticker && (
        <div className="rounded-xl border border-[#e5e5ea] bg-white p-4">
          {/* Header row */}
          <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="font-mono text-2xl font-bold text-[#1d1d1f]">{ticker}</p>
              {lastClose > 0 && (
                <div className="flex items-baseline gap-2">
                  <span className="font-mono text-xl text-[#1d1d1f]">${formatPrice(lastClose)}</span>
                  <span
                    className={`inline-flex items-center rounded-full px-2 py-0.5 font-mono text-sm font-semibold ${
                      isPositive
                        ? "bg-[#30d158]/10 text-[#30d158]"
                        : "bg-[#ff3b30]/10 text-[#ff3b30]"
                    }`}
                  >
                    {isPositive ? "+" : ""}{formatPrice(changeAbs)} ({isPositive ? "+" : ""}{change.toFixed(2)}%)
                  </span>
                  <span className="text-xs text-[#6e6e73]">{RANGE_CONFIG[range].label}</span>
                </div>
              )}
            </div>

            {/* Range pill buttons */}
            <div className="flex flex-wrap gap-1 rounded-xl bg-[#f5f5f7] p-1">
              {(Object.keys(RANGE_CONFIG) as Range[]).map((r) => (
                <button
                  key={r}
                  onClick={() => setRange(r)}
                  className={`rounded-lg px-3 py-1 text-xs font-semibold transition-colors ${
                    range === r
                      ? "bg-white text-[#1d1d1f] shadow-sm"
                      : "text-[#6e6e73] hover:text-[#1d1d1f]"
                  }`}
                >
                  {r}
                </button>
              ))}
            </div>
          </div>

          {/* Overlay toggles */}
          <div className="mb-3 flex flex-wrap gap-1.5">
            {(Object.keys(OVERLAY_LABELS) as OverlayKey[]).map((key) => (
              <button
                key={key}
                onClick={() => toggleOverlay(key)}
                className={`rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors ${
                  overlays.has(key)
                    ? "border-transparent text-white"
                    : "border-[#e5e5ea] text-[#6e6e73] hover:text-[#1d1d1f]"
                }`}
                style={overlays.has(key) ? { backgroundColor: OVERLAY_COLORS[key] } : undefined}
              >
                {OVERLAY_LABELS[key]}
              </button>
            ))}
          </div>

          {/* Chart */}
          {isLoading ? (
            <div className="flex h-72 items-center justify-center text-sm text-[#6e6e73]">
              Loading price data…
            </div>
          ) : isError ? (
            <div className="flex h-72 items-center justify-center text-sm text-[#ff3b30]">
              Failed to load data. Make sure the API is running and data has been collected.
            </div>
          ) : chartData.length === 0 ? (
            <div className="flex h-72 flex-col items-center justify-center gap-2 text-sm text-[#6e6e73]">
              <p>No price data for <span className="font-mono text-[#1d1d1f]">{ticker}</span> at {interval} interval.</p>
              <p className="text-xs">Try the 1Y or ALL range, or check that the ticker symbol is valid.</p>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={340}>
              <ComposedChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id="priceGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor={chartColor} stopOpacity={0.18} />
                    <stop offset="95%" stopColor={chartColor} stopOpacity={0}    />
                  </linearGradient>
                </defs>
                <XAxis
                  dataKey="time"
                  tick={{ fill: "#6e6e73", fontSize: 10 }}
                  tickLine={false}
                  axisLine={false}
                  minTickGap={70}
                  tickFormatter={tickFormatter}
                />
                <YAxis
                  yAxisId="price"
                  orientation="right"
                  domain={["auto", "auto"]}
                  tick={{ fill: "#6e6e73", fontSize: 10 }}
                  tickLine={false}
                  axisLine={false}
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
                  <ReferenceLine yAxisId="price" y={firstClose} stroke="#e5e5ea" strokeDasharray="4 3" />
                )}
                <Bar
                  yAxisId="vol"
                  dataKey="volume"
                  name="Volume"
                  fill={isPositive ? "#30d15820" : "#ff3b3020"}
                  radius={[2, 2, 0, 0]}
                />
                <Area
                  yAxisId="price"
                  type="monotone"
                  dataKey="close"
                  name="Close"
                  stroke={chartColor}
                  strokeWidth={1.5}
                  fill="url(#priceGrad)"
                  dot={false}
                  activeDot={{ r: 4, fill: chartColor }}
                />
                {overlays.has("sma20") && (
                  <Line yAxisId="price" type="monotone" dataKey="sma20"
                    name="SMA 20" stroke={OVERLAY_COLORS.sma20} strokeWidth={1.2}
                    dot={false} activeDot={false} legendType="none" connectNulls={false} />
                )}
                {overlays.has("sma50") && (
                  <Line yAxisId="price" type="monotone" dataKey="sma50"
                    name="SMA 50" stroke={OVERLAY_COLORS.sma50} strokeWidth={1.2}
                    dot={false} activeDot={false} legendType="none" connectNulls={false} />
                )}
                {overlays.has("sma200") && (
                  <Line yAxisId="price" type="monotone" dataKey="sma200"
                    name="SMA 200" stroke={OVERLAY_COLORS.sma200} strokeWidth={1.5}
                    dot={false} activeDot={false} legendType="none" connectNulls={false} />
                )}
                {overlays.has("ema9") && (
                  <Line yAxisId="price" type="monotone" dataKey="ema9"
                    name="EMA 9" stroke={OVERLAY_COLORS.ema9} strokeWidth={1.2}
                    strokeDasharray="4 2" dot={false} activeDot={false} legendType="none" connectNulls={false} />
                )}
                {overlays.has("ema21") && (
                  <Line yAxisId="price" type="monotone" dataKey="ema21"
                    name="EMA 21" stroke={OVERLAY_COLORS.ema21} strokeWidth={1.2}
                    strokeDasharray="4 2" dot={false} activeDot={false} legendType="none" connectNulls={false} />
                )}
                {overlays.has("bb") && (<>
                  <Line yAxisId="price" type="monotone" dataKey="bbUpper"
                    name="BB Upper" stroke={OVERLAY_COLORS.bb} strokeWidth={0.9}
                    strokeDasharray="3 2" dot={false} activeDot={false} legendType="none" connectNulls={false} />
                  <Line yAxisId="price" type="monotone" dataKey="bbMid"
                    name="BB Mid" stroke={OVERLAY_COLORS.bb} strokeWidth={0.9}
                    strokeDasharray="6 3" dot={false} activeDot={false} legendType="none" connectNulls={false} />
                  <Line yAxisId="price" type="monotone" dataKey="bbLower"
                    name="BB Lower" stroke={OVERLAY_COLORS.bb} strokeWidth={0.9}
                    strokeDasharray="3 2" dot={false} activeDot={false} legendType="none" connectNulls={false} />
                </>)}
              </ComposedChart>
            </ResponsiveContainer>
          )}
        </div>
      )}

      {/* Paper trade panel */}
      {ticker && <PaperTradePanel ticker={ticker} lastPrice={lastClose} />}
    </div>
  );
}
