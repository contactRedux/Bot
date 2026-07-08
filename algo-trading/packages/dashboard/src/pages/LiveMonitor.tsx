/**
 * pages/LiveMonitor.tsx — Real-time signal + fill + position monitor.
 *
 * Layout:
 *  - SignalTable (all 200 latest)
 *  - Fill log table
 *  - Open positions from portfolioStore
 */
import { useFillStore, usePortfolioStore } from "@/store";
import SignalTable from "@/components/SignalTable";

function FillLog() {
  const fills = useFillStore((s) => s.fills);

  const formatUsd = (n: number) =>
    n.toLocaleString("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });

  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-800">
      <div className="flex items-center justify-between border-b border-zinc-700 px-4 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
          Fill Log
        </span>
        <span className="rounded-full bg-zinc-700 px-2 py-0.5 font-mono text-xs text-zinc-400">
          {fills.length}
        </span>
      </div>

      {fills.length === 0 ? (
        <p className="py-8 text-center text-sm text-zinc-500">No fills yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-zinc-500">
                <th className="px-4 py-2">Time</th>
                <th className="px-4 py-2">Ticker</th>
                <th className="px-4 py-2">Side</th>
                <th className="px-4 py-2 text-right">Qty</th>
                <th className="px-4 py-2 text-right">Fill Price</th>
                <th className="px-4 py-2 text-right">Commission</th>
                <th className="px-4 py-2 text-right">Real P&L</th>
                <th className="px-4 py-2">Strategy</th>
              </tr>
            </thead>
            <tbody>
              {fills.map((f, i) => {
                const isBuy = f.side.toUpperCase().includes("BUY");
                return (
                  <tr key={i} className="border-t border-zinc-700/50 hover:bg-zinc-700/20">
                    <td className="px-4 py-2 font-mono text-xs text-zinc-500">
                      {new Date(f.timestamp).toLocaleTimeString()}
                    </td>
                    <td className="px-4 py-2 font-mono font-semibold text-sky-400">{f.ticker}</td>
                    <td className="px-4 py-2">
                      <span
                        className={`rounded px-2 py-0.5 text-xs font-medium ${
                          isBuy
                            ? "bg-emerald-500/15 text-emerald-400"
                            : "bg-rose-500/15 text-rose-400"
                        }`}
                      >
                        {f.side.toUpperCase()}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-zinc-300">{f.quantity}</td>
                    <td className="px-4 py-2 text-right font-mono text-zinc-300">
                      {formatUsd(f.fill_price)}
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-xs text-zinc-500">
                      {formatUsd(f.commission)}
                    </td>
                    <td
                      className={`px-4 py-2 text-right font-mono text-xs ${
                        f.realised_pnl >= 0 ? "text-emerald-400" : "text-rose-400"
                      }`}
                    >
                      {formatUsd(f.realised_pnl)}
                    </td>
                    <td className="px-4 py-2 text-xs text-zinc-500">{f.strategy_id}</td>
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

function PositionPanel() {
  const positions = usePortfolioStore((s) => s.snapshot?.positions ?? []);
  const snapshot = usePortfolioStore((s) => s.snapshot);

  const formatUsd = (n: number) =>
    n.toLocaleString("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });

  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-800">
      <div className="flex items-center justify-between border-b border-zinc-700 px-4 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
          Open Positions
        </span>
        {snapshot && (
          <span className="font-mono text-xs text-zinc-400">
            Cash: {formatUsd(snapshot.cash)}
          </span>
        )}
      </div>

      {positions.length === 0 ? (
        <p className="py-8 text-center text-sm text-zinc-500">No open positions.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-zinc-500">
                <th className="px-4 py-2">Ticker</th>
                <th className="px-4 py-2 text-right">Qty</th>
                <th className="px-4 py-2 text-right">Mark Price</th>
                <th className="px-4 py-2 text-right">Mkt Value</th>
                <th className="px-4 py-2 text-right">Unreal P&L</th>
                <th className="px-4 py-2 text-right">%</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <tr key={p.ticker} className="border-t border-zinc-700/50 hover:bg-zinc-700/20">
                  <td className="px-4 py-2 font-mono font-semibold text-sky-400">{p.ticker}</td>
                  <td className="px-4 py-2 text-right font-mono text-zinc-300">{p.quantity}</td>
                  <td className="px-4 py-2 text-right font-mono text-zinc-300">
                    {formatUsd(p.mark_price)}
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
                    {p.unrealised_pnl_pct >= 0 ? "+" : ""}
                    {p.unrealised_pnl_pct.toFixed(2)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function LiveMonitor() {
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-zinc-100">Live Monitor</h1>
      <PositionPanel />
      <SignalTable limit={200} />
      <FillLog />
    </div>
  );
}
