/**
 * pages/LiveMonitor.tsx — Real-time signal + fill + position monitor.
 * Apple light aesthetic.
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
    <div className="rounded-xl border border-[#e5e5ea] bg-white">
      <div className="flex items-center justify-between border-b border-[#e5e5ea] px-4 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-[#6e6e73]">
          Fill Log
        </span>
        <span className="rounded-full bg-[#f5f5f7] px-2 py-0.5 font-mono text-xs text-[#6e6e73]">
          {fills.length}
        </span>
      </div>

      {fills.length === 0 ? (
        <p className="py-8 text-center text-sm text-[#6e6e73]">No fills yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-[#6e6e73]">
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
                  <tr key={i} className="border-t border-[#e5e5ea] hover:bg-[#f5f5f7]">
                    <td className="px-4 py-2 font-mono text-xs text-[#6e6e73]">
                      {new Date(f.timestamp).toLocaleTimeString()}
                    </td>
                    <td className="px-4 py-2 font-mono font-semibold text-[#007aff]">{f.ticker}</td>
                    <td className="px-4 py-2">
                      <span
                        className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                          isBuy
                            ? "bg-[#30d158]/10 text-[#30d158]"
                            : "bg-[#ff3b30]/10 text-[#ff3b30]"
                        }`}
                      >
                        {f.side.toUpperCase()}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-[#3a3a3c]">{f.quantity}</td>
                    <td className="px-4 py-2 text-right font-mono text-[#3a3a3c]">
                      {formatUsd(f.fill_price)}
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-xs text-[#6e6e73]">
                      {formatUsd(f.commission)}
                    </td>
                    <td
                      className={`px-4 py-2 text-right font-mono text-xs ${
                        f.realised_pnl >= 0 ? "text-[#30d158]" : "text-[#ff3b30]"
                      }`}
                    >
                      {formatUsd(f.realised_pnl)}
                    </td>
                    <td className="px-4 py-2 text-xs text-[#6e6e73]">{f.strategy_id}</td>
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
    <div className="rounded-xl border border-[#e5e5ea] bg-white">
      <div className="flex items-center justify-between border-b border-[#e5e5ea] px-4 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-[#6e6e73]">
          Open Positions
        </span>
        {snapshot && (
          <span className="font-mono text-xs text-[#6e6e73]">
            Cash: {formatUsd(snapshot.cash)}
          </span>
        )}
      </div>

      {positions.length === 0 ? (
        <p className="py-8 text-center text-sm text-[#6e6e73]">No open positions.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-[#6e6e73]">
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
                <tr key={p.ticker} className="border-t border-[#e5e5ea] hover:bg-[#f5f5f7]">
                  <td className="px-4 py-2 font-mono font-semibold text-[#007aff]">{p.ticker}</td>
                  <td className="px-4 py-2 text-right font-mono text-[#3a3a3c]">{p.quantity}</td>
                  <td className="px-4 py-2 text-right font-mono text-[#3a3a3c]">
                    {formatUsd(p.mark_price)}
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
      <h1 className="text-xl font-semibold text-[#1d1d1f]">Live Monitor</h1>
      <PositionPanel />
      <SignalTable limit={200} />
      <FillLog />
    </div>
  );
}
