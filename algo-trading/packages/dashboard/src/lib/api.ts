/**
 * lib/api.ts — Typed fetch wrappers for the FastAPI backend.
 *
 * All paths use the /api/ prefix to match the FastAPI router prefixes.
 * In development, Vite proxies /api/* → http://localhost:8000/api/* (no rewrite).
 * VITE_API_BASE_URL can be set to override the base for non-local deployments.
 */

import type {
  BacktestRequest,
  BacktestResponse,
  BacktestStatusResponse,
  PortfolioResponse,
  PriceHistoryResponse,
  RiskStatusResponse,
  SignalsResponse,
  StrategiesResponse,
  StrategyInfo,
} from "./types";

const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${BASE}${path}`;
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Portfolio
// ---------------------------------------------------------------------------

export const fetchPortfolio = (): Promise<PortfolioResponse> =>
  request<PortfolioResponse>("/api/portfolio");

export const fetchPriceHistory = (
  ticker: string,
  interval = "1d",
  limit = 365,
): Promise<PriceHistoryResponse> =>
  request<PriceHistoryResponse>(
    `/api/portfolio/price-history?ticker=${encodeURIComponent(ticker)}&interval=${interval}&limit=${limit}`,
  );

// ---------------------------------------------------------------------------
// Signals
// ---------------------------------------------------------------------------

export const fetchSignals = (): Promise<SignalsResponse> =>
  request<SignalsResponse>("/api/signals");

// ---------------------------------------------------------------------------
// Risk
// ---------------------------------------------------------------------------

export const fetchRiskStatus = (): Promise<RiskStatusResponse> =>
  request<RiskStatusResponse>("/api/risk/status");

export const resumeTrading = (newEquity?: number): Promise<{ success: boolean; message: string }> =>
  request("/api/risk/resume", {
    method: "POST",
    body: JSON.stringify({ new_equity: newEquity ?? null }),
  });

// ---------------------------------------------------------------------------
// Strategies
// ---------------------------------------------------------------------------

export const fetchStrategies = (): Promise<StrategiesResponse> =>
  request<StrategiesResponse>("/api/strategies");

export const toggleStrategy = (
  id: string,
  enabled: boolean,
): Promise<StrategyInfo> =>
  request<StrategyInfo>(`/api/strategies/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ enabled }),
  });

// ---------------------------------------------------------------------------
// Backtest
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// News
// ---------------------------------------------------------------------------

export interface NewsArticleDTO {
  id: string;
  ticker: string;
  headline: string;
  source: string;
  sentiment_score: number;
  sentiment_label: "positive" | "negative" | "neutral";
  published_at: string;
  url: string | null;
}

export interface NewsResponse {
  articles: NewsArticleDTO[];
  count: number;
}

export const fetchNews = (ticker?: string, limit = 100): Promise<NewsResponse> => {
  const params = new URLSearchParams({ limit: String(limit) });
  if (ticker) params.set("ticker", ticker);
  return request<NewsResponse>(`/api/news?${params.toString()}`);
};

// ---------------------------------------------------------------------------
// Trading engine control
// ---------------------------------------------------------------------------

export interface TradingStatusResponse {
  running: boolean;
  trading_mode: string;
  bar_interval: string;
  tickers: string[];
  loop_count: number;
  last_processed: Record<string, string>;
  portfolio: { cash: number; total_equity: number };
}

export const fetchTradingStatus = (): Promise<TradingStatusResponse> =>
  request<TradingStatusResponse>("/api/trading/status");

export const startTrading = (): Promise<{ success: boolean; message: string }> =>
  request("/api/trading/start", { method: "POST" });

export const stopTrading = (): Promise<{ success: boolean; message: string }> =>
  request("/api/trading/stop", { method: "POST" });

export const haltTrading = (): Promise<{ success: boolean; message: string }> =>
  // Halt is triggered by sending a POST to risk/resume won't work for halt —
  // the monitor fires automatically, but operators can force-halt by calling
  // stop which stops the engine (no new orders) and separately
  // we expose a manual flag via trading/stop.
  stopTrading();

// ---------------------------------------------------------------------------
// Backtest
// ---------------------------------------------------------------------------

export const runBacktest = (body: BacktestRequest): Promise<BacktestResponse> =>
  request<BacktestResponse>("/api/backtest/run", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const fetchBacktestStatus = (runId: string): Promise<BacktestStatusResponse> =>
  request<BacktestStatusResponse>(`/api/backtest/${runId}/status`);

export const fetchBacktestResult = (runId: string): Promise<BacktestResponse> =>
  request<BacktestResponse>(`/api/backtest/${runId}`);

// ---------------------------------------------------------------------------
// Optimization
// ---------------------------------------------------------------------------

export interface OptimizeRequest {
  strategy: string;
  tickers: string[];
  start_date: string;
  end_date: string;
  interval?: string;
  n_trials?: number;
  objective?: string;
  initial_capital?: number;
}

export interface WalkForwardRequest {
  strategies: string[];
  tickers: string[];
  start_date: string;
  end_date: string;
  interval?: string;
  n_splits?: number;
  oos_size_days?: number;
  initial_capital?: number;
}

export const runOptimize = (body: OptimizeRequest): Promise<Record<string, unknown>> =>
  request<Record<string, unknown>>("/api/optimize/run", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const fetchOptimizeStatus = (runId: string): Promise<Record<string, unknown>> =>
  request<Record<string, unknown>>(`/api/optimize/${runId}/status`);

export const fetchOptimizeResult = (runId: string): Promise<Record<string, unknown>> =>
  request<Record<string, unknown>>(`/api/optimize/${runId}`);

export const runWalkForward = (body: WalkForwardRequest): Promise<Record<string, unknown>> =>
  request<Record<string, unknown>>("/api/backtest/walkforward", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const fetchWalkForwardResult = (runId: string): Promise<Record<string, unknown>> =>
  request<Record<string, unknown>>(`/api/optimize/${runId}`);
