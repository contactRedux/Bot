import { NavLink } from "react-router-dom";
import { useWsStore, useTradingStore } from "@/store";

/**
 * Top navigation bar for the trading terminal.
 * Shows WS connection status and live trading engine state.
 */
const links = [
  { to: "/", label: "Overview" },
  { to: "/chart", label: "Charts" },
  { to: "/watchlist", label: "Watchlist" },
  { to: "/analysis", label: "Analysis" },
  { to: "/ai", label: "AI Analyst" },
  { to: "/metrics", label: "Metrics" },
  { to: "/live", label: "Live" },
  { to: "/strategies", label: "Strategies" },
  { to: "/backtest", label: "Backtest" },
  { to: "/news", label: "News" },
  { to: "/risk", label: "Risk" },
];

export default function NavBar() {
  const connected    = useWsStore((s) => s.connected);
  const lastHeartbeat = useWsStore((s) => s.lastHeartbeat);
  const running      = useTradingStore((s) => s.running);
  const tradingMode  = useTradingStore((s) => s.tradingMode);

  const hbAge = lastHeartbeat
    ? Math.round((Date.now() - new Date(lastHeartbeat).getTime()) / 1_000)
    : null;

  return (
    <nav className="border-b border-zinc-700 bg-zinc-800 px-4">
      <div className="mx-auto flex max-w-screen-2xl items-center gap-6 py-3">
        {/* Brand */}
        <span className="font-mono text-sm font-bold tracking-widest text-sky-400">
          ALGO&#8209;TRADE
        </span>

        {/* Navigation links */}
        <div className="flex gap-4">
          {links.map(({ to, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                [
                  "rounded px-3 py-1 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-sky-500/20 text-sky-400"
                    : "text-zinc-400 hover:text-zinc-100",
                ].join(" ")
              }
            >
              {label}
            </NavLink>
          ))}
        </div>

        {/* Status indicators */}
        <div className="ml-auto flex items-center gap-4 text-xs">
          {/* Trading engine state */}
          <div className="flex items-center gap-1.5">
            <span
              className={`inline-block h-2 w-2 rounded-full transition-colors ${
                running ? "bg-emerald-400" : "bg-zinc-600"
              }`}
            />
            <span className={running ? "text-emerald-400" : "text-zinc-500"}>
              {running
                ? `TRADING · ${tradingMode.toUpperCase()}`
                : tradingMode === "dev"
                ? "ENGINE IDLE"
                : "ENGINE STOPPED"}
            </span>
          </div>

          {/* Divider */}
          <span className="text-zinc-700">|</span>

          {/* WebSocket connection */}
          <div className="flex items-center gap-1.5 text-zinc-500">
            <span
              className={`inline-block h-2 w-2 rounded-full transition-colors ${
                connected ? "bg-sky-400" : "bg-rose-400"
              }`}
            />
            <span className={connected ? "text-sky-400" : "text-zinc-500"}>
              {connected
                ? hbAge !== null
                  ? `WS · ${hbAge}s`
                  : "WS CONNECTED"
                : "WS DISCONNECTED"}
            </span>
          </div>
        </div>
      </div>
    </nav>
  );
}
