/**
 * pages/WatchlistPage.tsx — Persistent watchlist with price alerts.
 *
 * Features:
 *  - Add / remove tickers
 *  - Live price + 1-day change pulled from GET /api/portfolio/price-history
 *  - Per-ticker price alerts: above/below threshold stored in localStorage
 *  - Visual alert indicator when threshold is breached
 *  - All state (tickers + alerts) persisted in localStorage
 */
import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchPriceHistory } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types & storage keys
// ---------------------------------------------------------------------------

const WATCHLIST_KEY = "watchlist_tickers";
const ALERTS_KEY    = "watchlist_alerts";

interface Alert {
  direction: "above" | "below";
  threshold: number;
  triggered: boolean;
}

function loadTickers(): string[] {
  try {
    const raw = localStorage.getItem(WATCHLIST_KEY);
    if (raw !== null) {
      const p = JSON.parse(raw) as string[];
      if (Array.isArray(p)) return p;
    }
  } catch { /* ignore */ }
  return ["AAPL", "MSFT", "NVDA", "TSLA", "AMD"];
}

function saveTickers(t: string[]) {
  try { localStorage.setItem(WATCHLIST_KEY, JSON.stringify(t)); } catch { /* ignore */ }
}

function loadAlerts(): Record<string, Alert> {
  try {
    const raw = localStorage.getItem(ALERTS_KEY);
    if (raw) return JSON.parse(raw) as Record<string, Alert>;
  } catch { /* ignore */ }
  return {};
}

function saveAlerts(a: Record<string, Alert>) {
  try { localStorage.setItem(ALERTS_KEY, JSON.stringify(a)); } catch { /* ignore */ }
}

// ---------------------------------------------------------------------------
// Single ticker row
// ---------------------------------------------------------------------------
function TickerRow({
  ticker,
  alert,
  onRemove,
  onSetAlert,
  onClearAlert,
}: {
  ticker: string;
  alert: Alert | undefined;
  onRemove: () => void;
  onSetAlert: (dir: "above" | "below", threshold: number) => void;
  onClearAlert: () => void;
}) {
  const [showAlertForm, setShowAlertForm] = useState(false);
  const [alertDir, setAlertDir]           = useState<"above" | "below">("above");
  const [alertVal, setAlertVal]           = useState("");

  const { data } = useQuery({
    queryKey: ["wl-price", ticker],
    queryFn: () => fetchPriceHistory(ticker, "1d", 2),
    staleTime: 60_000,
    refetchInterval: 120_000,
  });

  const pts   = data?.points ?? [];
  const last  = pts[pts.length - 1]?.close ?? null;
  const prev  = pts.length >= 2 ? pts[pts.length - 2]?.close : null;
  const chg1d = last != null && prev != null ? ((last - prev) / prev) * 100 : null;

  // Check if alert is triggered
  const triggered = alert && last != null && (
    (alert.direction === "above" && last >= alert.threshold) ||
    (alert.direction === "below" && last <= alert.threshold)
  );

  function submitAlert(e: React.FormEvent) {
    e.preventDefault();
    const v = parseFloat(alertVal);
    if (!isNaN(v) && v > 0) {
      onSetAlert(alertDir, v);
      setShowAlertForm(false);
      setAlertVal("");
    }
  }

  return (
    <div className={`rounded-lg border p-4 transition-colors ${
      triggered ? "border-amber-500/50 bg-amber-500/5" : "border-zinc-700 bg-zinc-800"
    }`}>
      <div className="flex items-start justify-between gap-3">
        {/* Ticker + price */}
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-mono text-base font-bold text-zinc-100">{ticker}</span>
            {triggered && (
              <span className="rounded bg-amber-500/20 px-1.5 py-0.5 text-xs font-medium text-amber-400">
                ALERT
              </span>
            )}
          </div>
          <div className="mt-1 flex items-baseline gap-3">
            <span className="text-xl font-semibold text-zinc-200">
              {last != null ? `$${last.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—"}
            </span>
            {chg1d != null && (
              <span className={`text-sm ${chg1d >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                {chg1d >= 0 ? "+" : ""}{chg1d.toFixed(2)}%
              </span>
            )}
          </div>
          {/* Alert info */}
          {alert && (
            <div className="mt-1 flex items-center gap-1.5 text-xs text-zinc-500">
              <span className={triggered ? "text-amber-400" : "text-zinc-500"}>
                Alert: {alert.direction} ${alert.threshold.toLocaleString()}
              </span>
              <button
                onClick={onClearAlert}
                className="text-zinc-600 hover:text-zinc-400"
              >
                ×
              </button>
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex shrink-0 gap-2">
          <button
            onClick={() => setShowAlertForm(!showAlertForm)}
            className="rounded border border-zinc-600 px-2.5 py-1 text-xs text-zinc-400 hover:border-sky-600 hover:text-sky-400"
          >
            {alert ? "Edit Alert" : "+ Alert"}
          </button>
          <button
            onClick={onRemove}
            className="rounded border border-zinc-700 px-2.5 py-1 text-xs text-zinc-500 hover:border-rose-500/50 hover:text-rose-400"
          >
            ×
          </button>
        </div>
      </div>

      {/* Alert form */}
      {showAlertForm && (
        <form onSubmit={submitAlert} className="mt-3 flex gap-2">
          <select
            value={alertDir}
            onChange={(e) => setAlertDir(e.target.value as "above" | "below")}
            className="rounded border border-zinc-600 bg-zinc-700 px-2 py-1 text-xs text-zinc-200"
          >
            <option value="above">Above</option>
            <option value="below">Below</option>
          </select>
          <input
            type="number"
            step="any"
            min="0"
            value={alertVal}
            onChange={(e) => setAlertVal(e.target.value)}
            placeholder="Price threshold"
            className="flex-1 rounded border border-zinc-600 bg-zinc-700 px-2 py-1 text-xs text-zinc-200 placeholder-zinc-500 focus:border-sky-500 focus:outline-none"
          />
          <button
            type="submit"
            className="rounded bg-sky-700 px-3 py-1 text-xs text-white hover:bg-sky-600"
          >
            Set
          </button>
          <button
            type="button"
            onClick={() => setShowAlertForm(false)}
            className="rounded border border-zinc-600 px-2 py-1 text-xs text-zinc-400"
          >
            Cancel
          </button>
        </form>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
export default function WatchlistPage() {
  const [tickers, setTickers] = useState<string[]>(loadTickers);
  const [alerts, setAlerts]   = useState<Record<string, Alert>>(loadAlerts);
  const [input, setInput]     = useState("");

  // Persist on change
  useEffect(() => { saveTickers(tickers); }, [tickers]);
  useEffect(() => { saveAlerts(alerts); }, [alerts]);

  function addTicker(e: React.FormEvent) {
    e.preventDefault();
    const t = input.trim().toUpperCase();
    if (t && !tickers.includes(t)) {
      setTickers([...tickers, t]);
    }
    setInput("");
  }

  function removeTicker(t: string) {
    setTickers(tickers.filter((x) => x !== t));
    setAlerts((prev) => {
      const next = { ...prev };
      delete next[t];
      return next;
    });
  }

  function setAlert(ticker: string, dir: "above" | "below", threshold: number) {
    setAlerts((prev) => ({ ...prev, [ticker]: { direction: dir, threshold, triggered: false } }));
  }

  function clearAlert(ticker: string) {
    setAlerts((prev) => {
      const next = { ...prev };
      delete next[ticker];
      return next;
    });
  }

  const triggeredCount = Object.values(alerts).filter((a) => a.triggered).length;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">Watchlist</h1>
          <p className="text-sm text-zinc-500">
            Track prices and set alerts for any ticker. State is saved in your browser.
          </p>
        </div>
        {triggeredCount > 0 && (
          <span className="rounded bg-amber-500/20 px-2.5 py-1 text-sm font-medium text-amber-400">
            {triggeredCount} alert{triggeredCount > 1 ? "s" : ""} triggered
          </span>
        )}
      </div>

      {/* Add ticker */}
      <form onSubmit={addTicker} className="flex gap-3">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value.toUpperCase())}
          placeholder="Add ticker — e.g. MU, SNDK, AMD"
          className="flex-1 rounded border border-zinc-600 bg-zinc-800 px-4 py-2.5 text-sm text-zinc-100 placeholder-zinc-500 focus:border-sky-500 focus:outline-none"
        />
        <button
          type="submit"
          className="rounded bg-sky-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-sky-500"
        >
          Add
        </button>
      </form>

      {/* Tickers */}
      {tickers.length === 0 ? (
        <div className="rounded-lg border border-zinc-700 bg-zinc-800/50 py-20 text-center text-sm text-zinc-500">
          No tickers in your watchlist. Add one above.
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {tickers.map((t) => (
            <TickerRow
              key={t}
              ticker={t}
              alert={alerts[t]}
              onRemove={() => removeTicker(t)}
              onSetAlert={(dir, val) => setAlert(t, dir, val)}
              onClearAlert={() => clearAlert(t)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
