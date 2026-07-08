/**
 * lib/api.ts — Typed fetch wrappers for the FastAPI backend.
 *
 * Base URL is read from VITE_API_BASE_URL (defaults to http://localhost:8000).
 * During development, Vite proxies /api → http://localhost:8000 so calls
 * can use relative paths. We use the full base URL so this also works when
 * the app is served independently.
 */

import type {
  BacktestRequest,
  BacktestResponse,
  BacktestStatusResponse,
  PortfolioResponse,
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
  request<PortfolioResponse>("/portfolio/positions");

// ---------------------------------------------------------------------------
// Signals
// ---------------------------------------------------------------------------

export const fetchSignals = (): Promise<SignalsResponse> =>
  request<SignalsResponse>("/signals/latest");

// ---------------------------------------------------------------------------
// Risk
// ---------------------------------------------------------------------------

export const fetchRiskStatus = (): Promise<RiskStatusResponse> =>
  request<RiskStatusResponse>("/risk/status");

export const resumeTrading = (newEquity?: number): Promise<{ success: boolean; message: string }> =>
  request("/risk/resume", {
    method: "POST",
    body: JSON.stringify({ new_equity: newEquity ?? null }),
  });

// ---------------------------------------------------------------------------
// Strategies
// ---------------------------------------------------------------------------

export const fetchStrategies = (): Promise<StrategiesResponse> =>
  request<StrategiesResponse>("/strategies");

export const toggleStrategy = (
  id: string,
  enabled: boolean,
): Promise<StrategyInfo> =>
  request<StrategyInfo>(`/strategies/${id}/toggle`, {
    method: "PATCH",
    body: JSON.stringify({ enabled }),
  });

// ---------------------------------------------------------------------------
// Backtest
// ---------------------------------------------------------------------------

export const runBacktest = (body: BacktestRequest): Promise<BacktestResponse> =>
  request<BacktestResponse>("/backtest/run", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const fetchBacktestStatus = (runId: string): Promise<BacktestStatusResponse> =>
  request<BacktestStatusResponse>(`/backtest/status/${runId}`);

export const fetchBacktestResult = (runId: string): Promise<BacktestResponse> =>
  request<BacktestResponse>(`/backtest/result/${runId}`);
