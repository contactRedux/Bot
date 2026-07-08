/**
 * components/PortfolioSummary.tsx
 *
 * Equity curve chart + key metric stat cards.
 * Reads from portfolioStore (populated via REST and WS portfolio_update events).
 */
import { useQuery } from "@tanstack/react-query";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
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
  positive?: boolean | null; // null = neutral
  subtext?: string;
}

function StatCard({ label, value, positive, subtext }: StatCardProps) {
  const valueColor =
    positive === null || positive === undefined
      ? "text-zinc-100"
      : positive
        ? "text-emerald-400"
        : "text-rose-400";

  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-4">
      <p className="text-xs text-zinc-500">{label}</p>
      <p className={`mt-1 font-mono text-2xl font-bold ${valueColor}`}>{value}</p>
      {subtext && <p className="mt-0.5 text-xs text-zinc-500">{subtext}</p>}
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
    <div className="rounded border border-zinc-600 bg-zinc-800 px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 font-mono text-zinc-400">{label}</p>
      <p className="font-mono font-semibold text-sky-400">
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

  // Equity curve: prefer WS rolling curve, fall back to empty
  const chartData = equityCurve.map((pt) => ({
    time: pt.timestamp.slice(0, 16).replace("T", " "),
    equity: pt.equity,
  }));

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
      <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-4">
        <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-zinc-500">
          Equity Curve
        </h3>
        {isLoading && chartData.length === 0 ? (
          <p className="py-12 text-center text-sm text-zinc-500">Loading…</p>
        ) : chartData.length === 0 ? (
          <p className="py-12 text-center text-sm text-zinc-500">
            No equity data yet — data streams in via the WebSocket feed.
          </p>
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#38bdf8" stopOpacity={0.25} />
                  <stop offset="95%" stopColor="#38bdf8" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#3f3f46" />
              <XAxis
                dataKey="time"
                tick={{ fill: "#a1a1aa", fontSize: 10 }}
                tickLine={false}
                minTickGap={80}
              />
              <YAxis
                domain={["auto", "auto"]}
                tick={{ fill: "#a1a1aa", fontSize: 10 }}
                tickLine={false}
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
                stroke="#38bdf8"
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
        <div className="rounded-lg border border-zinc-700 bg-zinc-800">
          <div className="border-b border-zinc-700 px-4 py-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">
            Open Positions
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-zinc-500">
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
                  <tr key={p.ticker} className="border-t border-zinc-700/50 hover:bg-zinc-700/30">
                    <td className="px-4 py-2 font-mono font-semibold text-sky-400">{p.ticker}</td>
                    <td className="px-4 py-2 text-right font-mono text-zinc-300">{p.quantity}</td>
                    <td className="px-4 py-2 text-right font-mono text-zinc-300">
                      ${p.avg_cost.toFixed(2)}
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-zinc-300">
                      ${p.mark_price.toFixed(2)}
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-zinc-300">
                      {formatUsd(p.market_value)}
                    </td>
                    <td
                      className={`px-4 py-2 text-right font-mono ${
                        p.unrealised_pnl >= 0 ? "text-emerald-400" : "text-rose-400"
                      }`}
                    >
                      {formatUsd(p.unrealised_pnl)}
                    </td>
                    <td
                      className={`px-4 py-2 text-right font-mono text-xs ${
                        p.unrealised_pnl_pct >= 0 ? "text-emerald-400" : "text-rose-400"
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
