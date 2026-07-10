import { NavLink } from "react-router-dom";
import { useState, useEffect } from "react";
import { useWsStore, useTradingStore } from "@/store";

/**
 * Top navigation bar — Apple Stocks light aesthetic.
 * White bar, black text, blue active-link underline.
 */
const links = [
  { to: "/", label: "Overview" },
  { to: "/chart", label: "Charts" },
  { to: "/watchlist", label: "Watchlist" },
  { to: "/analysis", label: "Analysis" },
  { to: "/ai", label: "AI Analyst" },
  { to: "/metrics", label: "Metrics" },
  { to: "/live", label: "Live" },
  { to: "/bot", label: "Bot Analysis" },
  { to: "/strategies", label: "Strategies" },
  { to: "/backtest", label: "Backtest" },
  { to: "/news", label: "News" },
  { to: "/risk", label: "Risk" },
];

export default function NavBar() {
  const connected     = useWsStore((s) => s.connected);
  const lastHeartbeat = useWsStore((s) => s.lastHeartbeat);
  const running       = useTradingStore((s) => s.running);
  const tradingMode   = useTradingStore((s) => s.tradingMode);

  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 1_000);
    return () => clearInterval(id);
  }, []);

  const hbAge = lastHeartbeat
    ? Math.round((Date.now() - new Date(lastHeartbeat).getTime()) / 1_000)
    : null;

  return (
    <nav className="border-b border-[#e5e5ea] bg-white px-4">
      <div className="mx-auto flex max-w-screen-2xl items-center gap-6 py-3">
        {/* Brand */}
        <span className="font-mono text-sm font-bold tracking-widest text-[#007aff]">
          ALGO&#8209;TRADE
        </span>

        {/* Navigation links */}
        <div className="flex gap-1">
          {links.map(({ to, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                [
                  "px-3 py-1 text-sm font-medium transition-colors border-b-2",
                  isActive
                    ? "border-[#007aff] text-[#007aff]"
                    : "border-transparent text-[#6e6e73] hover:text-[#1d1d1f]",
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
                running ? "bg-[#30d158]" : "bg-[#c7c7cc]"
              }`}
            />
            <span className={running ? "text-[#30d158]" : "text-[#8e8e93]"}>
              {running
                ? `TRADING · ${tradingMode.toUpperCase()}`
                : tradingMode === "dev"
                ? "ENGINE IDLE"
                : "ENGINE STOPPED"}
            </span>
          </div>

          {/* Divider */}
          <span className="text-[#e5e5ea]">|</span>

          {/* WebSocket connection */}
          <div className="flex items-center gap-1.5">
            <span
              className={`inline-block h-2 w-2 rounded-full transition-colors ${
                connected ? "bg-[#007aff]" : "bg-[#ff3b30]"
              }`}
            />
            <span className={connected ? "text-[#007aff]" : "text-[#8e8e93]"}>
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
