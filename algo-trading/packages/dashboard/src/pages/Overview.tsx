/**
 * pages/Overview.tsx — Portfolio Overview page.
 *
 * Sections:
 *  - PortfolioSummary  (equity curve + stat cards + positions table)
 *  - PriceChart        (for the most recently held ticker)
 *  - SignalTable       (last 20 signals)
 */
import { usePortfolioStore } from "@/store";
import PortfolioSummary from "@/components/PortfolioSummary";
import PriceChart from "@/components/PriceChart";
import SignalTable from "@/components/SignalTable";

export default function Overview() {
  // Pick the first held ticker for the price chart (or show a placeholder)
  const positions = usePortfolioStore((s) => s.snapshot?.positions ?? []);
  const primaryTicker = positions.length > 0 ? positions[0].ticker : null;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-zinc-100">Portfolio Overview</h1>
      </div>

      {/* Portfolio summary */}
      <PortfolioSummary />

      {/* Price chart for primary held ticker */}
      {primaryTicker ? (
        <PriceChart ticker={primaryTicker} data={[]} />
      ) : (
        <div className="rounded-lg border border-zinc-700 bg-zinc-800 px-4 py-8 text-center text-sm text-zinc-500">
          No open positions — price chart will appear once positions are taken.
        </div>
      )}

      {/* Recent signals */}
      <SignalTable limit={20} />
    </div>
  );
}
