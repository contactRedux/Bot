import { NavLink } from "react-router-dom";

/**
 * Top navigation bar for the trading terminal.
 * Uses react-router-dom NavLink so the active route gets a highlighted style.
 */
const links = [
  { to: "/", label: "Overview" },
  { to: "/live", label: "Live" },
  { to: "/backtest", label: "Backtest" },
  { to: "/news", label: "News" },
  { to: "/risk", label: "Risk" },
];

export default function NavBar() {
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

        {/* Status indicator — will be wired to WebSocket state in Sub-Task 10 */}
        <div className="ml-auto flex items-center gap-2 text-xs text-zinc-500">
          <span className="inline-block h-2 w-2 rounded-full bg-zinc-600" />
          <span>DISCONNECTED</span>
        </div>
      </div>
    </nav>
  );
}
