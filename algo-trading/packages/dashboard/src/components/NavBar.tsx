import { NavLink } from "react-router-dom";
import { useWsStore } from "@/store";

/**
 * Top navigation bar for the trading terminal.
 * Uses react-router-dom NavLink for active route highlighting.
 * WS connection indicator is wired to the wsStore.
 */
const links = [
  { to: "/", label: "Overview" },
  { to: "/live", label: "Live" },
  { to: "/backtest", label: "Backtest" },
  { to: "/news", label: "News" },
  { to: "/risk", label: "Risk" },
];

export default function NavBar() {
  const connected = useWsStore((s) => s.connected);
  const lastHeartbeat = useWsStore((s) => s.lastHeartbeat);

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

        {/* WebSocket status indicator */}
        <div className="ml-auto flex items-center gap-2 text-xs text-zinc-500">
          <span
            className={`inline-block h-2 w-2 rounded-full transition-colors ${
              connected ? "bg-emerald-400" : "bg-rose-400"
            }`}
          />
          <span className={connected ? "text-emerald-400" : "text-zinc-500"}>
            {connected
              ? hbAge !== null
                ? `LIVE · ${hbAge}s`
                : "CONNECTED"
              : "DISCONNECTED"}
          </span>
        </div>
      </div>
    </nav>
  );
}
