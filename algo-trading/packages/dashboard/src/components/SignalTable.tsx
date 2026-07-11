/**
 * components/SignalTable.tsx
 *
 * Live-updating table of the latest strategy signals — Apple light aesthetic.
 */
import { useSignalStore } from "@/store";
import type { Signal } from "@/store";

function StrengthBar({ value }: { value: number }) {
  const pct = Math.round(Math.abs(value) * 100);
  const color = value >= 0 ? "bg-[#30d158]" : "bg-[#ff3b30]";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-[#f5f5f7]">
        <div className={`h-full ${color} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-8 text-right font-mono text-xs text-[#6e6e73]">
        {value >= 0 ? "+" : ""}
        {value.toFixed(2)}
      </span>
    </div>
  );
}

function relTime(isoTs: string): string {
  const diffMs = Date.now() - new Date(isoTs).getTime();
  const secs = Math.floor(diffMs / 1_000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  return `${Math.floor(mins / 60)}h ago`;
}

interface Props {
  limit?: number;
  /** When true, hide signals for crypto tickers (those containing "-") */
  equityOnly?: boolean;
}

export default function SignalTable({ limit = 50, equityOnly = false }: Props) {
  const signals = useSignalStore((s) => s.signals);
  const filtered = equityOnly ? signals.filter((s) => !s.ticker.includes("-")) : signals;
  const rows: Signal[] = filtered.slice(0, limit);

  return (
    <div className="rounded-xl border border-[#e5e5ea] bg-white">
      <div className="flex items-center justify-between border-b border-[#e5e5ea] px-4 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-[#6e6e73]">
          Live Signals
        </span>
        <span className="rounded-full bg-[#f5f5f7] px-2 py-0.5 font-mono text-xs text-[#6e6e73]">
          {filtered.length}
        </span>
      </div>

      {rows.length === 0 ? (
        <p className="py-8 text-center text-sm text-[#6e6e73]">
          Waiting for signal events…
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-[#6e6e73]">
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
                    className="border-t border-[#e5e5ea] transition-colors hover:bg-[#f5f5f7]"
                  >
                    <td className="px-4 py-2 font-mono font-semibold text-[#007aff]">
                      {s.ticker}
                    </td>
                    <td className="px-4 py-2 text-[#6e6e73]">{s.strategy_id}</td>
                    <td className="px-4 py-2">
                      <span
                        className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${
                          isLong
                            ? "bg-[#30d158]/10 text-[#30d158]"
                            : "bg-[#ff3b30]/10 text-[#ff3b30]"
                        }`}
                      >
                        {isLong ? "▲ LONG" : "▼ SHORT"}
                      </span>
                    </td>
                    <td className="px-4 py-2">
                      <StrengthBar value={s.signal} />
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-xs text-[#6e6e73]">
                      {(s.confidence * 100).toFixed(0)}%
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-xs text-[#8e8e93]">
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
