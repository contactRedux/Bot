/**
 * Overview page — portfolio summary, equity curve, and current positions.
 * Full implementation is in Sub-Task 10.
 */
export default function Overview() {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-zinc-100">Portfolio Overview</h1>
      <p className="text-sm text-zinc-400">
        Full implementation in Sub-Task 10 — see plan for component breakdown.
      </p>
      <div className="grid grid-cols-4 gap-4">
        {["Total Return", "Sharpe Ratio", "Max Drawdown", "Win Rate"].map((label) => (
          <div key={label} className="rounded-lg border border-zinc-700 bg-zinc-800 p-4">
            <p className="text-xs text-zinc-500">{label}</p>
            <p className="mt-1 text-2xl font-mono font-bold text-zinc-100">—</p>
          </div>
        ))}
      </div>
    </div>
  );
}
