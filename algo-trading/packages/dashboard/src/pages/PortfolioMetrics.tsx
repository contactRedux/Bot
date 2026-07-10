/**
 * pages/PortfolioMetrics.tsx — Live session performance metrics dashboard.
 * Apple light aesthetic.
 */
import { useQuery } from "@tanstack/react-query";
import { fetchPortfolioMetrics } from "@/lib/api";
import type { PortfolioMetricsResponse } from "@/lib/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(n: number | null | undefined, decimals = 2): string {
  if (n == null) return "—";
  return n.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function pctFmt(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : ""}${fmt(v)}%`;
}

function colorPct(v: number | null | undefined) {
  if (v == null) return "text-[#6e6e73]";
  return v >= 0 ? "text-[#30d158]" : "text-[#ff3b30]";
}

function MetricCard({
  label,
  value,
  sub,
  valueClass,
}: {
  label: string;
  value: string;
  sub?: string;
  valueClass?: string;
}) {
  return (
    <div className="rounded-xl border border-[#e5e5ea] bg-white p-4">
      <div className="text-xs font-medium uppercase tracking-wider text-[#6e6e73]">{label}</div>
      <div className={`mt-1.5 text-2xl font-bold ${valueClass ?? "text-[#1d1d1f]"}`}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-[#6e6e73]">{sub}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Strategy attribution bar
// ---------------------------------------------------------------------------
function AttributionBar({ sid, pnl, total }: { sid: string; pnl: number; total: number }) {
  const pct = total !== 0 ? Math.abs((pnl / total) * 100) : 0;
  return (
    <div>
      <div className="flex justify-between text-xs">
        <span className="text-[#6e6e73]">{sid}</span>
        <span className={pnl >= 0 ? "text-[#30d158]" : "text-[#ff3b30]"}>
          {pnl >= 0 ? "+" : ""}${fmt(pnl)} ({fmt(pct, 1)}%)
        </span>
      </div>
      <div className="mt-1 h-1.5 rounded-full bg-[#f5f5f7]">
        <div
          className={`h-1.5 rounded-full ${pnl >= 0 ? "bg-[#30d158]" : "bg-[#ff3b30]"}`}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
export default function PortfolioMetrics() {
  const { data, isFetching, error, refetch } = useQuery<PortfolioMetricsResponse>({
    queryKey: ["portfolio-metrics"],
    queryFn: fetchPortfolioMetrics,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  const hasData = data && !("error" in data);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold text-[#1d1d1f]">Portfolio Metrics</h1>
          <p className="text-sm text-[#6e6e73]">
            Performance statistics for the current live session.
          </p>
        </div>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="rounded-lg border border-[#e5e5ea] px-3 py-1.5 text-xs text-[#6e6e73] hover:border-[#007aff] hover:text-[#007aff] disabled:opacity-50"
        >
          {isFetching ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {/* Error */}
      {(error || (data && "error" in (data as object))) && (
        <div className="rounded-xl border border-[#ff3b30]/20 bg-[#ff3b30]/5 px-4 py-3 text-sm text-[#ff3b30]">
          {(error as Error | null)?.message ?? String((data as unknown as { error: string }).error)}
        </div>
      )}

      {/* Loading skeleton */}
      {isFetching && !data && (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="h-24 animate-pulse rounded-xl bg-[#f5f5f7]" />
          ))}
        </div>
      )}

      {/* Metrics */}
      {hasData && (
        <>
          {/* Return & growth row */}
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            <MetricCard
              label="Total Return"
              value={pctFmt(data.total_return_pct)}
              sub={`$${fmt(data.total_pnl)} PnL`}
              valueClass={colorPct(data.total_return_pct)}
            />
            <MetricCard
              label="CAGR"
              value={pctFmt(data.cagr_pct)}
              sub="Annualised"
              valueClass={colorPct(data.cagr_pct)}
            />
            <MetricCard
              label="Final Equity"
              value={`$${fmt(data.final_equity)}`}
              sub={`Started $${fmt(data.initial_capital)}`}
            />
            <MetricCard
              label="Session Length"
              value={`${data.n_calendar_days}d`}
              sub={data.start_date ? new Date(data.start_date).toLocaleDateString() : undefined}
            />
          </div>

          {/* Risk-adjusted row */}
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            <MetricCard
              label="Sharpe Ratio"
              value={fmt(data.sharpe_ratio, 3)}
              sub="Annualised (rf=5%)"
              valueClass={data.sharpe_ratio >= 1 ? "text-[#30d158]" : data.sharpe_ratio >= 0 ? "text-[#1d1d1f]" : "text-[#ff3b30]"}
            />
            <MetricCard
              label="Sortino Ratio"
              value={fmt(data.sortino_ratio, 3)}
              sub="Downside deviation"
              valueClass={data.sortino_ratio >= 1 ? "text-[#30d158]" : data.sortino_ratio >= 0 ? "text-[#1d1d1f]" : "text-[#ff3b30]"}
            />
            <MetricCard
              label="Max Drawdown"
              value={pctFmt(-Math.abs(data.max_drawdown_pct))}
              sub="Peak-to-trough"
              valueClass="text-[#ff3b30]"
            />
            <MetricCard
              label="Calmar Ratio"
              value={fmt(data.calmar_ratio, 3)}
              sub="CAGR / |MaxDD|"
            />
          </div>

          {/* Trade stats row */}
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            <MetricCard
              label="Win Rate"
              value={pctFmt(data.win_rate_pct)}
              sub={`${data.n_wins}W / ${data.n_losses}L`}
              valueClass={data.win_rate_pct >= 50 ? "text-[#30d158]" : "text-[#ff3b30]"}
            />
            <MetricCard
              label="Profit Factor"
              value={fmt(data.profit_factor, 3)}
              sub="Gross wins / gross losses"
              valueClass={data.profit_factor >= 1.5 ? "text-[#30d158]" : data.profit_factor >= 1 ? "text-[#1d1d1f]" : "text-[#ff3b30]"}
            />
            <MetricCard
              label="Total Trades"
              value={String(data.n_trades)}
              sub={`Avg $${fmt(data.avg_trade_pnl, 2)} / trade`}
            />
            <MetricCard
              label="Annual Volatility"
              value={pctFmt(data.annual_volatility_pct)}
              sub="Annualised std dev"
            />
          </div>

          {/* Strategy attribution */}
          {data.strategy_attribution && Object.keys(data.strategy_attribution).length > 0 && (
            <div className="rounded-xl border border-[#e5e5ea] bg-white p-5">
              <h2 className="mb-4 text-sm font-medium text-[#1d1d1f]">Strategy Attribution</h2>
              <div className="space-y-3">
                {Object.entries(data.strategy_attribution)
                  .sort(([, a], [, b]) => b - a)
                  .map(([sid, pnl]) => (
                    <AttributionBar
                      key={sid}
                      sid={sid}
                      pnl={pnl}
                      total={Math.abs(data.total_pnl) || 1}
                    />
                  ))}
              </div>
            </div>
          )}

          {data.n_trades === 0 && (
            <div className="rounded-xl border border-[#e5e5ea] bg-[#f5f5f7] py-10 text-center text-sm text-[#6e6e73]">
              No trades recorded in this session yet — metrics will populate as fills arrive.
            </div>
          )}
        </>
      )}
    </div>
  );
}
