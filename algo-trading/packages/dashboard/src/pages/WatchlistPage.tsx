/**
 * pages/WatchlistPage.tsx — Persistent watchlist with price alerts — Apple light aesthetic.
 */
import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchPriceHistory } from "@/lib/api";

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
    <div className={`rounded-xl border p-4 transition-colors ${
      triggered ? "border-amber-400/40 bg-amber-50" : "border-[#e5e5ea] bg-white"
    }`}>
      <div className="flex items-start justify-between gap-3">
        {/* Ticker + price */}
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-mono text-base font-bold text-[#1d1d1f]">{ticker}</span>
            {triggered && (
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-600">
                ALERT
              </span>
            )}
          </div>
          <div className="mt-1 flex items-baseline gap-3">
            <span className="text-xl font-semibold text-[#1d1d1f]">
              {last != null ? `$${last.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—"}
            </span>
            {chg1d != null && (
              <span
                className={`inline-flex items-center rounded-full px-2 py-0.5 text-sm font-medium ${
                  chg1d >= 0
                    ? "bg-[#30d158]/10 text-[#30d158]"
                    : "bg-[#ff3b30]/10 text-[#ff3b30]"
                }`}
              >
                {chg1d >= 0 ? "+" : ""}{chg1d.toFixed(2)}%
              </span>
            )}
          </div>
          {alert && (
            <div className="mt-1 flex items-center gap-1.5 text-xs text-[#6e6e73]">
              <span className={triggered ? "text-amber-500" : "text-[#6e6e73]"}>
                Alert: {alert.direction} ${alert.threshold.toLocaleString()}
              </span>
              <button onClick={onClearAlert} className="text-[#8e8e93] hover:text-[#ff3b30]">
                ×
              </button>
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex shrink-0 gap-2">
          <button
            onClick={() => setShowAlertForm(!showAlertForm)}
            className="rounded-lg border border-[#e5e5ea] px-2.5 py-1 text-xs text-[#6e6e73] hover:border-[#007aff] hover:text-[#007aff]"
          >
            {alert ? "Edit Alert" : "+ Alert"}
          </button>
          <button
            onClick={onRemove}
            className="rounded-lg border border-[#e5e5ea] px-2.5 py-1 text-xs text-[#8e8e93] hover:border-[#ff3b30]/40 hover:text-[#ff3b30]"
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
            className="rounded-lg border border-[#e5e5ea] bg-[#f5f5f7] px-2 py-1 text-xs text-[#1d1d1f]"
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
            className="flex-1 rounded-lg border border-[#e5e5ea] bg-[#f5f5f7] px-2 py-1 text-xs text-[#1d1d1f] placeholder-[#8e8e93] focus:border-[#007aff] focus:outline-none"
          />
          <button
            type="submit"
            className="rounded-lg bg-[#007aff] px-3 py-1 text-xs text-white hover:bg-[#007aff]/90"
          >
            Set
          </button>
          <button
            type="button"
            onClick={() => setShowAlertForm(false)}
            className="rounded-lg border border-[#e5e5ea] px-2 py-1 text-xs text-[#6e6e73]"
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
          <h1 className="text-xl font-semibold text-[#1d1d1f]">Watchlist</h1>
          <p className="text-sm text-[#6e6e73]">
            Track prices and set alerts for any ticker. State is saved in your browser.
          </p>
        </div>
        {triggeredCount > 0 && (
          <span className="rounded-full bg-amber-100 px-3 py-1 text-sm font-medium text-amber-600">
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
          className="flex-1 rounded-xl border border-[#e5e5ea] bg-[#f5f5f7] px-4 py-2.5 text-sm text-[#1d1d1f] placeholder-[#8e8e93] focus:border-[#007aff] focus:outline-none"
        />
        <button
          type="submit"
          className="rounded-xl bg-[#007aff] px-5 py-2.5 text-sm font-medium text-white hover:bg-[#007aff]/90"
        >
          Add
        </button>
      </form>

      {/* Tickers */}
      {tickers.length === 0 ? (
        <div className="rounded-xl border border-[#e5e5ea] bg-[#f5f5f7] py-20 text-center text-sm text-[#6e6e73]">
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
