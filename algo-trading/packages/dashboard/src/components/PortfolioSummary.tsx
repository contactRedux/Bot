/**
 * components/PortfolioSummary.tsx
 *
 * Equity curve chart + key metric stat cards — Apple light aesthetic.
 */
import { useQuery } from "@tanstack/react-query";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { fetchPortfolio } from "@/lib/api";
import { usePortfolioStore } from "@/store";

// ---------------------------------------------------------------------------
// Stat card
// ---------------------------------------------------------------------------

interface StatCardProps {
  label: string;
  value: string;
  positive?: boolean | null;
  subtext?: string;
}

function StatCard({ label, value, positive, subtext }: StatCardProps) {
  const valueColor =
    positive === null || positive === undefined
      ? "text-[#1d1d1f]"
      : positive
        ? "text-[#30d158]"
        : "text-[#ff3b30]";

  return (
    <div className="rounded-xl border border-[#e5e5ea] bg-white p-4">
      <p className="text-xs font-medium uppercase tracking-wider text-[#6e6e73]">{label}</p>
      <p className={`mt-1 font-mono text-2xl font-bold ${valueColor}`}>{value}</p>
      {subtext && <p className="mt-0.5 text-xs text-[#6e6e73]">{subtext}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tooltip
// ---------------------------------------------------------------------------

const CurveTooltip = ({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ value: number }>;
  label?: string;
}) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-[#e5e5ea] bg-white px-3 py-2 text-xs shadow-sm">
      <p className="mb-1 font-mono text-[#6e6e73]">{label}</p>
      <p className="font-mono font-semibold text-[#007aff]">
        ${payload[0].value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </p>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function PortfolioSummary() {
  const { data, isLoading } = useQuery({
    queryKey: ["portfolio"],
    queryFn: fetchPortfolio,
    refetchInterval: 10_000,
  });

  const equityCurve = usePortfolioStore((s) => s.equityCurve);

  const snapshot = data;

  const totalPnl = snapshot
    ? snapshot.total_unrealised_pnl + snapshot.total_realised_pnl
    : null;

  const formatUsd = (n: number) =>
    n.toLocaleString("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    });

  const formatPct = (n: number) =>
    `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;

  const chartData = equityCurve.map((pt) => ({
    time: pt.timestamp.slice(0, 16).replace("T", " "),
    equity: pt.equity,
  }));

  // Determine chart colour by first vs last equity
  const firstEq = chartData[0]?.equity ?? 0;
  const lastEq  = chartData[chartData.length - 1]?.equity ?? 0;
  const chartPositive = lastEq >= firstEq;
  const chartColor = chartPositive ? "#30d158" : "#ff3b30";

  return (
    <div className="space-y-4">
      {/* Stat cards */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard
          label="Portfolio Equity"
          value={snapshot ? formatUsd(snapshot.total_equity) : "—"}
          positive={null}
        />
        <StatCard
          label="Unrealised P&L"
          value={snapshot ? formatUsd(snapshot.total_unrealised_pnl) : "—"}
          positive={snapshot ? snapshot.total_unrealised_pnl >= 0 : null}
        />
        <StatCard
          label="Realised P&L"
          value={snapshot ? formatUsd(snapshot.total_realised_pnl) : "—"}
          positive={snapshot ? snapshot.total_realised_pnl >= 0 : null}
        />
        <StatCard
          label="Total P&L"
          value={totalPnl !== null ? formatUsd(totalPnl) : "—"}
          positive={totalPnl !== null ? totalPnl >= 0 : null}
          subtext={snapshot ? `Cash: ${formatUsd(snapshot.cash)}` : undefined}
        />
      </div>

      {/* Equity curve */}
      <div className="rounded-xl border border-[#e5e5ea] bg-white p-4">
        <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-[#6e6e73]">
          Equity Curve
        </h3>
        {isLoading && chartData.length === 0 ? (
          <p className="py-12 text-center text-sm text-[#6e6e73]">Loading…</p>
        ) : chartData.length === 0 ? (
          <p className="py-12 text-center text-sm text-[#6e6e73]">
            No equity data yet — data streams in via the WebSocket feed.
          </p>
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor={chartColor} stopOpacity={0.18} />
                  <stop offset="95%" stopColor={chartColor} stopOpacity={0}    />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="time"
                tick={{ fill: "#6e6e73", fontSize: 10 }}
                tickLine={false}
                axisLine={false}
                minTickGap={80}
              />
              <YAxis
                domain={["auto", "auto"]}
                tick={{ fill: "#6e6e73", fontSize: 10 }}
                tickLine={false}
                axisLine={false}
                width={64}
                tickFormatter={(v: number) =>
                  v >= 1_000_000
                    ? `$${(v / 1_000_000).toFixed(1)}M`
                    : `$${(v / 1_000).toFixed(0)}k`
                }
              />
              <Tooltip content={<CurveTooltip />} />
              <Area
                type="monotone"
                dataKey="equity"
                stroke={chartColor}
                strokeWidth={1.5}
                fill="url(#equityGrad)"
                dot={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* Positions table */}
      {snapshot && snapshot.positions.length > 0 && (
        <div className="rounded-xl border border-[#e5e5ea] bg-white">
          <div className="border-b border-[#e5e5ea] px-4 py-2 text-xs font-semibold uppercase tracking-wider text-[#6e6e73]">
            Open Positions
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-[#6e6e73]">
                  <th className="px-4 py-2">Ticker</th>
                  <th className="px-4 py-2 text-right">Qty</th>
                  <th className="px-4 py-2 text-right">Avg Cost</th>
                  <th className="px-4 py-2 text-right">Mark Price</th>
                  <th className="px-4 py-2 text-right">Mkt Value</th>
                  <th className="px-4 py-2 text-right">Unreal P&L</th>
                  <th className="px-4 py-2 text-right">%</th>
                </tr>
              </thead>
              <tbody>
                {snapshot.positions.map((p) => (
                  <tr key={p.ticker} className="border-t border-[#e5e5ea] hover:bg-[#f5f5f7]">
                    <td className="px-4 py-2 font-mono font-semibold text-[#007aff]">{p.ticker}</td>
                    <td className="px-4 py-2 text-right font-mono text-[#3a3a3c]">{p.quantity}</td>
                    <td className="px-4 py-2 text-right font-mono text-[#3a3a3c]">
                      ${p.avg_cost.toFixed(2)}
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-[#3a3a3c]">
                      ${p.mark_price.toFixed(2)}
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-[#3a3a3c]">
                      {formatUsd(p.market_value)}
                    </td>
                    <td
                      className={`px-4 py-2 text-right font-mono ${
                        p.unrealised_pnl >= 0 ? "text-[#30d158]" : "text-[#ff3b30]"
                      }`}
                    >
                      {formatUsd(p.unrealised_pnl)}
                    </td>
                    <td
                      className={`px-4 py-2 text-right font-mono text-xs ${
                        p.unrealised_pnl_pct >= 0 ? "text-[#30d158]" : "text-[#ff3b30]"
                      }`}
                    >
                      {formatPct(p.unrealised_pnl_pct)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
