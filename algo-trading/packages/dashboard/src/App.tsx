import { Routes, Route } from "react-router-dom";
import NavBar from "@/components/NavBar";
import AiAnalyst from "@/pages/AiAnalyst";
import BotAnalysis from "@/pages/BotAnalysis";
import Overview from "@/pages/Overview";
import BacktestExplorer from "@/pages/BacktestExplorer";
import LiveMonitor from "@/pages/LiveMonitor";
import NewsFeed from "@/pages/NewsFeed";
import PortfolioMetrics from "@/pages/PortfolioMetrics";
import RiskDashboard from "@/pages/RiskDashboard";
import Strategies from "@/pages/Strategies";
import StockChart from "@/pages/StockChart";
import TickerAnalysis from "@/pages/TickerAnalysis";
import WatchlistPage from "@/pages/WatchlistPage";
import { useWebSocketFeed } from "@/hooks/useWebSocketFeed";

/**
 * Root application component.
 *
 * Route map:
 *   /           → Overview          (portfolio summary + equity curve)
 *   /chart      → StockChart        (Yahoo-Finance-style price chart with time ranges)
 *   /analysis   → TickerAnalysis    (composite technical analysis for any ticker)
 *   /metrics    → PortfolioMetrics  (Sharpe, max drawdown, win rate etc.)
 *   /watchlist  → WatchlistPage     (persistent watchlist + price alerts)
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
    <div className="min-h-screen bg-white text-[#1d1d1f]">
      <NavBar />
      <main className="mx-auto max-w-screen-2xl px-4 py-6">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/chart" element={<StockChart />} />
          <Route path="/analysis" element={<TickerAnalysis />} />
          <Route path="/ai" element={<AiAnalyst />} />
          <Route path="/metrics" element={<PortfolioMetrics />} />
          <Route path="/watchlist" element={<WatchlistPage />} />
          <Route path="/backtest" element={<BacktestExplorer />} />
          <Route path="/live" element={<LiveMonitor />} />
          <Route path="/bot" element={<BotAnalysis />} />
          <Route path="/news" element={<NewsFeed />} />
          <Route path="/risk" element={<RiskDashboard />} />
          <Route path="/strategies" element={<Strategies />} />
        </Routes>
      </main>
    </div>
  );
}
