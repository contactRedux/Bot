/**
 * components/RiskPanel.tsx
 *
 * Displays current risk metrics:
 *  - VaR (95% and 99%) and CVaR
 *  - Current drawdown gauge / progress bar
 *  - Daily loss bar
 *  - Correlation pairs at concentration threshold
 *  - HALT banner when trading is suspended
 *
 * Data is fetched via GET /risk/status (10 s poll) and merged with
 * real-time risk_alert events already in riskStore.
 */
import { useQuery } from "@tanstack/react-query";
import { fetchRiskStatus, resumeTrading } from "@/lib/api";
import { useRiskStore } from "@/store";

// ---------------------------------------------------------------------------
// Gauge bar (percentage)
// ---------------------------------------------------------------------------

interface GaugeBarProps {
  label: string;
  value: number;
  limit: number;
  unit?: string;
  invertColors?: boolean; // if true, higher = worse
}

function GaugeBar({ label, value, limit, unit = "%", invertColors = true }: GaugeBarProps) {
  const pct = limit > 0 ? Math.min((value / limit) * 100, 100) : 0;
  const danger = pct > 80;
  const warn   = pct > 50;

  const barColor = invertColors
    ? danger ? "bg-rose-500" : warn ? "bg-amber-400" : "bg-emerald-400"
    : "bg-sky-400";

  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="text-zinc-400">{label}</span>
        <span className={`font-mono font-semibold ${danger ? "text-rose-400" : warn ? "text-amber-400" : "text-zinc-200"}`}>
          {value.toFixed(2)}{unit}
          <span className="ml-1 text-zinc-500">/ {limit.toFixed(2)}{unit}</span>
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-zinc-700">
        <div
          className={`h-full transition-all ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Metric row
// ---------------------------------------------------------------------------

function MetricRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between py-1.5 text-sm">
      <span className="text-zinc-400">{label}</span>
      <span className="font-mono font-semibold text-zinc-100">{value}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function RiskPanel() {
  const storeStatus = useRiskStore((s) => s.status);
  const setStatus = useRiskStore((s) => s.setStatus);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["risk"],
    queryFn: fetchRiskStatus,
    refetchInterval: 10_000,
  });

  // Merge REST data into store
  if (data && !storeStatus) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    setStatus(data as any);
  }

  const s = storeStatus ?? data;

  const formatUsd = (n: number) =>
    n.toLocaleString("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    });

  const handleResume = async () => {
    await resumeTrading();
    await refetch();
  };

  return (
    <div className="space-y-4">
      {/* HALT banner */}
      {s?.halted && (
        <div className="flex items-center justify-between rounded-lg border border-rose-500/40 bg-rose-500/10 px-4 py-3">
          <div>
            <p className="font-semibold text-rose-400">🛑 TRADING HALTED</p>
            <p className="text-xs text-rose-300/70">{s.halt_reason || "Risk limit breached"}</p>
          </div>
          <button
            onClick={handleResume}
            className="rounded border border-rose-400/40 px-3 py-1 text-xs font-medium text-rose-400 transition hover:bg-rose-500/20"
          >
            Resume
          </button>
        </div>
      )}

      {isLoading && !s && (
        <p className="py-8 text-center text-sm text-zinc-500">Loading risk data…</p>
      )}

      {s && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {/* VaR / CVaR */}
          <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-4">
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-zinc-500">
              Value at Risk (Historical Simulation)
            </h3>
            <div className="divide-y divide-zinc-700/50">
              <MetricRow label="VaR 95%" value={formatUsd(s.var_95)} />
              <MetricRow label="VaR 99%" value={formatUsd(s.var_99)} />
              <MetricRow label="CVaR 95% (Expected Shortfall)" value={formatUsd(s.cvar_95)} />
              <MetricRow label="CVaR 99%" value={formatUsd(s.cvar_99)} />
              <MetricRow label="Peak Equity" value={formatUsd(s.peak_equity)} />
            </div>
          </div>

          {/* Drawdown gauges */}
          <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-4">
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-zinc-500">
              Drawdown &amp; Daily Loss
            </h3>
            <div className="space-y-4">
              <GaugeBar
                label="Current Drawdown"
                value={s.current_drawdown_pct}
                limit={s.max_drawdown_pct_limit}
              />
              <GaugeBar
                label="Daily Loss"
                value={s.daily_loss_pct}
                limit={s.max_daily_loss_pct_limit}
              />
            </div>
          </div>

          {/* Correlation pairs */}
          {s.correlation_pairs.length > 0 && (
            <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-4 lg:col-span-2">
              <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-zinc-500">
                High-Correlation Pairs
              </h3>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-zinc-500">
                      <th className="py-1.5 pr-4">Asset A</th>
                      <th className="py-1.5 pr-4">Asset B</th>
                      <th className="py-1.5 text-right">Correlation</th>
                    </tr>
                  </thead>
                  <tbody>
                    {s.correlation_pairs.map((p, i) => (
                      <tr key={i} className="border-t border-zinc-700/50">
                        <td className="py-1.5 pr-4 font-mono text-sky-400">{p.asset_a}</td>
                        <td className="py-1.5 pr-4 font-mono text-sky-400">{p.asset_b}</td>
                        <td
                          className={`py-1.5 text-right font-mono font-semibold ${
                            Math.abs(p.correlation) > 0.8 ? "text-rose-400" : "text-amber-400"
                          }`}
                        >
                          {p.correlation.toFixed(3)}
                        </td>
                      </tr>
                    ))}
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
