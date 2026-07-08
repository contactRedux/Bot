/**
 * pages/RiskDashboard.tsx — Risk management dashboard.
 *
 * Full RiskPanel with live polling and WS risk_alert integration.
 */
import RiskPanel from "@/components/RiskPanel";

export default function RiskDashboard() {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-zinc-100">Risk Dashboard</h1>
      <RiskPanel />
    </div>
  );
}
