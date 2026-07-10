/**
 * lib/api.ts — Typed fetch wrappers for the FastAPI backend.
 *
 * All paths use the /api/ prefix to match the FastAPI router prefixes.
 * In development, Vite proxies /api/* → http://localhost:8000/api/* (no rewrite).
 * VITE_API_BASE_URL can be set to override the base for non-local deployments.
 */

import type {
  AnalysisResponse,
  BacktestRequest,
  BacktestResponse,
  BacktestStatusResponse,
  PortfolioMetricsResponse,
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

export const fetchNewsForTicker = (
  ticker: string,
  limit = 20,
): Promise<NewsResponse & { inserted: number }> =>
  request<NewsResponse & { inserted: number }>("/api/news/fetch", {
    method: "POST",
    body: JSON.stringify({ ticker, limit }),
  });

// ---------------------------------------------------------------------------
// Manual paper trade
// ---------------------------------------------------------------------------

export interface ManualOrderRequest {
  ticker: string;
  side: "buy" | "sell";
  quantity: number;
  order_type?: "market" | "limit";
  limit_price?: number | null;
}

export interface ManualOrderResponse {
  success: boolean;
  status: string;
  ticker: string;
  side: string;
  quantity: number;
  fill_price: number;
  commission: number;
  broker_order_id: string;
}

export const submitManualOrder = (body: ManualOrderRequest): Promise<ManualOrderResponse> =>
  request<ManualOrderResponse>("/api/trading/order", {
    method: "POST",
    body: JSON.stringify(body),
  });

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

// ---------------------------------------------------------------------------
// Analysis
// ---------------------------------------------------------------------------

export const fetchAnalysis = (ticker: string): Promise<AnalysisResponse> =>
  request<AnalysisResponse>(`/api/analysis/${encodeURIComponent(ticker)}`);

// ---------------------------------------------------------------------------
// Portfolio metrics
// ---------------------------------------------------------------------------

export const fetchPortfolioMetrics = (): Promise<PortfolioMetricsResponse> =>
  request<PortfolioMetricsResponse>("/api/portfolio/metrics");

// ---------------------------------------------------------------------------
// Bot Analysis
// ---------------------------------------------------------------------------

export interface BotTickerItem {
  ticker: string;
  price: number;
  pct_change_1d: number;
  pct_change_1m: number;
  technical_rating: "Strong Buy" | "Buy" | "Hold" | "Sell" | "Strong Sell";
  technical_score: number;
  confidence_pct: number;
  position_status: "flat" | "long" | "short";
  position_qty: number;
  last_signal: {
    direction: "buy" | "sell" | null;
    confidence: number | null;
    timestamp: string | null;
    strategy_id: string | null;
  } | null;
  analyst_consensus: {
    total_analysts: number;
    strong_buy: number;
    buy: number;
    hold: number;
    sell: number;
    strong_sell: number;
    consensus_rating: string | null;
    consensus_score: number | null;
    target_price_avg: number | null;
    target_price_high: number | null;
    target_price_low: number | null;
  } | null;
  upside_to_target_pct: number | null;
  signal_scores: Record<string, number>;
}

export interface BotWatchlistResponse {
  tickers: BotTickerItem[];
  count: number;
  engine_running: boolean;
  loop_count: number;
  as_of: string;
}

export const fetchBotWatchlist = (): Promise<BotWatchlistResponse> =>
  request<BotWatchlistResponse>("/api/bot/watchlist");

// ---------------------------------------------------------------------------
// AI Analyst
// ---------------------------------------------------------------------------

export const fetchAiAnalysis = (body: import("./types").AiAnalyseRequest): Promise<import("./types").AiAnalystReport> =>
  request<import("./types").AiAnalystReport>("/api/ai/analyse", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const fetchAiHistory = (limit = 10): Promise<{ reports: import("./types").AiAnalystReport[]; count: number }> =>
  request(`/api/ai/history?limit=${limit}`);
