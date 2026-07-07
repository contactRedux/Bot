/**
 * Zustand store stubs.
 *
 * Each slice will be fully implemented in Sub-Task 10.
 * They are defined here so other modules can import from them
 * without import errors during development.
 */
import { create } from "zustand";

// ── Types (will be expanded in Sub-Task 10) ──────────────────────────────────

export interface Signal {
  ticker: string;
  strategy: string;
  signal: number;       // -1 to +1
  confidence: number;   // 0 to 1
  timestamp: string;    // ISO 8601
}

export interface NewsArticle {
  id: string;
  ticker: string;
  headline: string;
  source: string;
  sentiment_score: number;   // -1 to +1 from FinBERT
  sentiment_label: "positive" | "negative" | "neutral";
  published_at: string;
}

export interface PortfolioSnapshot {
  total_value: number;
  cash: number;
  unrealized_pnl: number;
  realized_pnl: number;
  equity_curve: { timestamp: string; value: number }[];
}

export interface RiskStatus {
  var_95: number;
  var_99: number;
  cvar_95: number;
  current_drawdown: number;
  max_drawdown: number;
  trading_halted: boolean;
}

// ── Store slices ─────────────────────────────────────────────────────────────

interface SignalStore {
  signals: Signal[];
  addSignal: (s: Signal) => void;
  clearSignals: () => void;
}

export const useSignalStore = create<SignalStore>((set) => ({
  signals: [],
  addSignal: (s) => set((state) => ({ signals: [s, ...state.signals].slice(0, 200) })),
  clearSignals: () => set({ signals: [] }),
}));

interface NewsStore {
  articles: NewsArticle[];
  addArticle: (a: NewsArticle) => void;
}

export const useNewsStore = create<NewsStore>((set) => ({
  articles: [],
  addArticle: (a) => set((state) => ({ articles: [a, ...state.articles].slice(0, 500) })),
}));

interface PortfolioStore {
  snapshot: PortfolioSnapshot | null;
  setSnapshot: (s: PortfolioSnapshot) => void;
}

export const usePortfolioStore = create<PortfolioStore>((set) => ({
  snapshot: null,
  setSnapshot: (s) => set({ snapshot: s }),
}));

interface RiskStore {
  status: RiskStatus | null;
  setStatus: (s: RiskStatus) => void;
}

export const useRiskStore = create<RiskStore>((set) => ({
  status: null,
  setStatus: (s) => set({ status: s }),
}));
