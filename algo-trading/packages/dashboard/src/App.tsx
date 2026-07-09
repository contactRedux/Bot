import { Routes, Route } from "react-router-dom";
import NavBar from "@/components/NavBar";
import Overview from "@/pages/Overview";
import BacktestExplorer from "@/pages/BacktestExplorer";
import LiveMonitor from "@/pages/LiveMonitor";
import NewsFeed from "@/pages/NewsFeed";
import RiskDashboard from "@/pages/RiskDashboard";
import Strategies from "@/pages/Strategies";
import { useWebSocketFeed } from "@/hooks/useWebSocketFeed";

/**
 * Root application component.
 *
 * Route map:
 *   /           → Overview          (portfolio summary + equity curve)
 *   /backtest   → BacktestExplorer  (configure + run backtests, view results)
 *   /live       → LiveMonitor       (real-time signals + fills + positions)
 *   /news       → NewsFeed          (headlines + FinBERT sentiment scores)
 *   /risk       → RiskDashboard     (VaR, CVaR, drawdown, correlation map)
 *   /strategies → Strategies        (engine control + strategy toggles)
 *
 * The WebSocket feed is initialised here (single connection for the entire app).
 */
export default function App() {
  // Single WebSocket connection shared across all pages via Zustand stores
  useWebSocketFeed();

  return (
    <div className="min-h-screen bg-zinc-900 text-zinc-100">
      <NavBar />
      <main className="mx-auto max-w-screen-2xl px-4 py-6">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/backtest" element={<BacktestExplorer />} />
          <Route path="/live" element={<LiveMonitor />} />
          <Route path="/news" element={<NewsFeed />} />
          <Route path="/risk" element={<RiskDashboard />} />
          <Route path="/strategies" element={<Strategies />} />
        </Routes>
      </main>
    </div>
  );
}
