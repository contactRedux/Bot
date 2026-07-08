/**
 * components/SignalTable.tsx
 *
 * Live-updating table of the latest strategy signals.
 * Data is pulled from signalStore (populated by the WebSocket "signal" events)
 * and augmented by an initial REST fetch of the latest signals.
 *
 * Columns: Ticker · Strategy · Direction · Strength · Confidence · Time
 * Color coded: LONG rows tinted emerald, SHORT rows tinted rose.
 */
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchSignals } from "@/lib/api";
import { useSignalStore } from "@/store";
import type { Signal } from "@/store";

// Progress bar for signal strength (maps [-1, +1] → visual bar)
function StrengthBar({ value }: { value: number }) {
  const pct = Math.round(Math.abs(value) * 100);
  const color = value >= 0 ? "bg-emerald-400" : "bg-rose-400";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-zinc-700">
        <div className={`h-full ${color} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-8 text-right font-mono text-xs text-zinc-400">
        {value >= 0 ? "+" : ""}
        {value.toFixed(2)}
      </span>
    </div>
  );
}

// Relative time display
function relTime(isoTs: string): string {
  const diffMs = Date.now() - new Date(isoTs).getTime();
  const secs = Math.floor(diffMs / 1_000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  return `${Math.floor(mins / 60)}h ago`;
}

interface Props {
  /** Max rows to render (default 50) */
  limit?: number;
}

export default function SignalTable({ limit = 50 }: Props) {
  const signals = useSignalStore((s) => s.signals);
  const addSignal = useSignalStore((s) => s.addSignal);

  // Seed the store from REST on mount
  const { data } = useQuery({
    queryKey: ["signals"],
    queryFn: fetchSignals,
    refetchInterval: 10_000,
  });

  useEffect(() => {
    if (data?.signals) {
      data.signals.forEach((s) =>
        addSignal({
          ticker: s.ticker,
          strategy_id: s.strategy_id,
          signal: s.signal,
          confidence: s.confidence,
          timestamp: s.timestamp,
        }),
      );
    }
  }, [data, addSignal]);

  const rows: Signal[] = signals.slice(0, limit);

  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-800">
      <div className="flex items-center justify-between border-b border-zinc-700 px-4 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
          Live Signals
        </span>
        <span className="rounded-full bg-zinc-700 px-2 py-0.5 font-mono text-xs text-zinc-400">
          {signals.length}
        </span>
      </div>

      {rows.length === 0 ? (
        <p className="py-8 text-center text-sm text-zinc-500">
          Waiting for signal events…
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-zinc-500">
                <th className="px-4 py-2">Ticker</th>
                <th className="px-4 py-2">Strategy</th>
                <th className="px-4 py-2">Direction</th>
                <th className="px-4 py-2">Strength</th>
                <th className="px-4 py-2 text-right">Conf</th>
                <th className="px-4 py-2 text-right">Age</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s, i) => {
                const isLong = s.signal >= 0;
                return (
                  <tr
                    key={i}
                    className={`border-t border-zinc-700/50 transition-colors ${
                      isLong ? "hover:bg-emerald-900/10" : "hover:bg-rose-900/10"
                    }`}
                  >
                    <td className="px-4 py-2 font-mono font-semibold text-sky-400">
                      {s.ticker}
                    </td>
                    <td className="px-4 py-2 text-zinc-400">{s.strategy_id}</td>
                    <td className="px-4 py-2">
                      <span
                        className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium ${
                          isLong
                            ? "bg-emerald-500/15 text-emerald-400"
                            : "bg-rose-500/15 text-rose-400"
                        }`}
                      >
                        {isLong ? "▲ LONG" : "▼ SHORT"}
                      </span>
                    </td>
                    <td className="px-4 py-2">
                      <StrengthBar value={s.signal} />
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-xs text-zinc-400">
                      {(s.confidence * 100).toFixed(0)}%
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-xs text-zinc-500">
                      {relTime(s.timestamp)}
                    </td>
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
