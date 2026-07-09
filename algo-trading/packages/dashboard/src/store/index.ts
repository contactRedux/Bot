/**
 * store/index.ts — Zustand slices for all real-time state.
 *
 * Slices
 * ------
 *  signalStore       Live strategy signals from WS "signal" events
 *  newsStore         News articles from WS payload (fed via REST poll or future WS event)
 *  portfolioStore    Latest portfolio snapshot + rolling equity curve
 *  riskStore         Current risk status (VaR, drawdown, halt flag)
 *  fillStore         Recent order fill events
 *  wsStore           WebSocket meta-state (connected, last heartbeat)
 *
 * The WebSocket event envelope from the server:
 *   { event_type: "bar"|"signal"|"fill"|"risk_alert"|"portfolio_update"|
 *                 "heartbeat"|"backtest_progress", payload: {...}, timestamp: "..." }
 */
import { create } from "zustand";
import type { WSEventType } from "@/lib/types";

// ---------------------------------------------------------------------------
// Re-export types
// ---------------------------------------------------------------------------

export interface Signal {
  ticker: string;
  strategy_id: string;
  signal: number;      // -1 to +1
  confidence: number;  // 0 to 1
  timestamp: string;
}

export interface NewsArticle {
  id: string;
  ticker: string;
  headline: string;
  source: string;
  sentiment_score: number;
  sentiment_label: "positive" | "negative" | "neutral";
  published_at: string;
}

export interface PortfolioSnapshot {
  cash: number;
  total_equity: number;
  total_market_value: number;
  total_unrealised_pnl: number;
  total_realised_pnl: number;
  positions: Array<{
    ticker: string;
    quantity: number;
    avg_cost: number;
    mark_price: number;
    market_value: number;
    unrealised_pnl: number;
    unrealised_pnl_pct: number;
  }>;
  last_updated: string;
}

export interface EquityCurvePoint {
  timestamp: string;
  equity: number;
}

export interface RiskStatus {
  halted: boolean;
  halt_reason: string;
  peak_equity: number;
  current_drawdown_pct: number;
  daily_loss_pct: number;
  max_drawdown_pct_limit: number;
  max_daily_loss_pct_limit: number;
  var_95: number;
  var_99: number;
  cvar_95: number;
  cvar_99: number;
  correlation_pairs: Array<{ asset_a: string; asset_b: string; correlation: number }>;
}

export interface FillEvent {
  ticker: string;
  side: string;
  quantity: number;
  fill_price: number;
  commission: number;
  realised_pnl: number;
  strategy_id: string;
  timestamp: string;
}

// ---------------------------------------------------------------------------
// Signal slice — keeps latest 200 signals, most recent first
// ---------------------------------------------------------------------------

interface SignalStore {
  signals: Signal[];
  addSignal: (s: Signal) => void;
  clearSignals: () => void;
}

export const useSignalStore = create<SignalStore>((set) => ({
  signals: [],
  addSignal: (s) =>
    set((state) => ({
      signals: [s, ...state.signals].slice(0, 200),
    })),
  clearSignals: () => set({ signals: [] }),
}));

// ---------------------------------------------------------------------------
// News slice — keeps latest 500 articles, most recent first
// ---------------------------------------------------------------------------

interface NewsStore {
  articles: NewsArticle[];
  addArticle: (a: NewsArticle) => void;
  setArticles: (articles: NewsArticle[]) => void;
}

export const useNewsStore = create<NewsStore>((set) => ({
  articles: [],
  addArticle: (a) =>
    set((state) => ({
      articles: [a, ...state.articles].slice(0, 500),
    })),
  setArticles: (articles) => set({ articles }),
}));

// ---------------------------------------------------------------------------
// Portfolio slice — latest snapshot + rolling equity curve (last 1 000 pts)
// ---------------------------------------------------------------------------

interface PortfolioStore {
  snapshot: PortfolioSnapshot | null;
  equityCurve: EquityCurvePoint[];
  setSnapshot: (s: PortfolioSnapshot) => void;
  appendEquityPoint: (pt: EquityCurvePoint) => void;
  setEquityCurve: (curve: EquityCurvePoint[]) => void;
}

export const usePortfolioStore = create<PortfolioStore>((set) => ({
  snapshot: null,
  equityCurve: [],
  setSnapshot: (s) => set({ snapshot: s }),
  appendEquityPoint: (pt) =>
    set((state) => ({
      equityCurve: [...state.equityCurve, pt].slice(-1_000),
    })),
  setEquityCurve: (curve) => set({ equityCurve: curve }),
}));

// ---------------------------------------------------------------------------
// Risk slice
// ---------------------------------------------------------------------------

interface RiskStore {
  status: RiskStatus | null;
  setStatus: (s: RiskStatus) => void;
}

export const useRiskStore = create<RiskStore>((set) => ({
  status: null,
  setStatus: (s) => set({ status: s }),
}));

// ---------------------------------------------------------------------------
// Fill slice — keeps last 500 fills
// ---------------------------------------------------------------------------

interface FillStore {
  fills: FillEvent[];
  addFill: (f: FillEvent) => void;
  clearFills: () => void;
}

export const useFillStore = create<FillStore>((set) => ({
  fills: [],
  addFill: (f) =>
    set((state) => ({
      fills: [f, ...state.fills].slice(0, 500),
    })),
  clearFills: () => set({ fills: [] }),
}));

// ---------------------------------------------------------------------------
// WebSocket meta-state (connection status, last heartbeat)
// ---------------------------------------------------------------------------

interface WsStore {
  connected: boolean;
  lastHeartbeat: string | null;
  lastEventType: WSEventType | null;
  setConnected: (v: boolean) => void;
  setLastHeartbeat: (ts: string) => void;
  setLastEventType: (t: WSEventType) => void;
}

export const useWsStore = create<WsStore>((set) => ({
  connected: false,
  lastHeartbeat: null,
  lastEventType: null,
  setConnected: (v) => set({ connected: v }),
  setLastHeartbeat: (ts) => set({ lastHeartbeat: ts }),
  setLastEventType: (t) => set({ lastEventType: t }),
}));

// ---------------------------------------------------------------------------
// Trading engine state slice
// ---------------------------------------------------------------------------

interface TradingStore {
  running: boolean;
  tradingMode: string;
  loopCount: number;
  setRunning: (v: boolean) => void;
  setTradingMode: (m: string) => void;
  setLoopCount: (n: number) => void;
}

export const useTradingStore = create<TradingStore>((set) => ({
  running: false,
  tradingMode: "dev",
  loopCount: 0,
  setRunning: (v) => set({ running: v }),
  setTradingMode: (m) => set({ tradingMode: m }),
  setLoopCount: (n) => set({ loopCount: n }),
}));
