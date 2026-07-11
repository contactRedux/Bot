/**
 * lib/types.ts — Shared TypeScript types mirroring api/schemas.py.
 *
 * All shapes match the FastAPI response models exactly so that API responses
 * can be cast directly without a translation layer.
 */

// ---------------------------------------------------------------------------
// WebSocket event envelope (mirrors WSEvent in api/schemas.py)
// ---------------------------------------------------------------------------

export type WSEventType =
  | "bar"
  | "signal"
  | "fill"
  | "risk_alert"
  | "portfolio_update"
  | "heartbeat"
  | "backtest_progress"
  | "news"
  | "trading_status"
  | "engine_tick";

export interface WSEvent {
  event_type: WSEventType;
  payload: Record<string, unknown>;
  timestamp: string;
}

// ---------------------------------------------------------------------------
// Portfolio
// ---------------------------------------------------------------------------

export interface PositionItem {
  ticker: string;
  quantity: number;
  avg_cost: number;
  mark_price: number;
  market_value: number;
  unrealised_pnl: number;
  unrealised_pnl_pct: number;
}

export interface PortfolioResponse {
  cash: number;
  total_equity: number;
  total_market_value: number;
  total_unrealised_pnl: number;
  total_realised_pnl: number;
  positions: PositionItem[];
  last_updated: string;
}

export interface EquityCurvePoint {
  timestamp: string;
  equity: number;
}

// ---------------------------------------------------------------------------
// Signals
// ---------------------------------------------------------------------------

export interface SignalItem {
  ticker: string;
  strategy_id: string;
  signal: number;      // [-1, +1]
  confidence: number;  // [0, 1]
  timestamp: string;
}

export interface SignalsResponse {
  signals: SignalItem[];
  count: number;
}

// ---------------------------------------------------------------------------
// Risk
// ---------------------------------------------------------------------------

export interface RiskStatusResponse {
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

// ---------------------------------------------------------------------------
// Backtest
// ---------------------------------------------------------------------------

export interface BacktestRequest {
  tickers: string[];
  start_date: string;
  end_date: string;
  strategies?: string[];
  initial_capital?: number;
  interval?: string;
}

export interface MetricsSummary {
  total_return_pct: number;
  cagr_pct: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  calmar_ratio: number;
  max_drawdown_pct: number;
  annual_volatility_pct: number;
  n_trades: number;
  win_rate_pct: number;
  profit_factor: number;
  avg_trade_pnl: number;
  final_equity: number;
}

export interface BacktestResponse {
  run_id: string;
  status: "completed" | "running" | "failed";
  metrics: Partial<MetricsSummary>;
  equity_curve: EquityCurvePoint[];
  trade_log: Record<string, unknown>[];
  strategy_attribution: Record<string, number>;
  tickers: string[];
  initial_capital: number;
  bar_interval: string;
  halted: boolean;
  halt_reason: string;
  created_at: string;
  error?: string | null;
}

export interface BacktestStatusResponse {
  run_id: string;
  status: "completed" | "running" | "failed" | "not_found";
  progress_pct: number;
  message: string;
}

// ---------------------------------------------------------------------------
// Strategies
// ---------------------------------------------------------------------------

export interface StrategyInfo {
  strategy_id: string;
  display_name: string;
  description: string;
  enabled: boolean;
  allocation_weight: number;
  tickers: string[];
}

export interface StrategiesResponse {
  strategies: StrategyInfo[];
  total: number;
}

// ---------------------------------------------------------------------------
// Price history (from GET /api/portfolio/price-history)
// ---------------------------------------------------------------------------

export interface PriceHistoryPoint {
  time: string;     // "YYYY-MM-DD" for daily bars, "YYYY-MM-DDTHH:MM" for intraday
  close: number;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  volume?: number | null;
}

export interface PriceHistoryResponse {
  ticker: string;
  interval: string;
  points: PriceHistoryPoint[];
  count: number;
}

// ---------------------------------------------------------------------------
// News (from store — shape comes via WS feed payload)
// ---------------------------------------------------------------------------

export interface NewsArticle {
  id: string;
  ticker: string;
  headline: string;
  source: string;
  sentiment_score: number;
  sentiment_label: "positive" | "negative" | "neutral";
  published_at: string;
}

// ---------------------------------------------------------------------------
// Analysis (from GET /api/analysis/{ticker})
// ---------------------------------------------------------------------------

export interface AnalysisIndicators {
  rsi: number;
  macd_line: number;
  macd_signal: number;
  bb_upper: number;
  bb_mid: number;
  bb_lower: number;
  sma_20: number;
  sma_50: number;
  sma_200: number;
  ema_9: number;
  ema_21: number;
  atr: number;
  stoch_k: number;
  stoch_d: number;
  williams_r: number;
  cci: number;
  obv: number;
  vwap_20: number;
  volume_ratio: number;
}

export interface AnalysisSignalScores {
  rsi: number;
  macd: number;
  bollinger: number;
  ma_trend: number;
  short_trend: number;
  stochastic: number;
  williams_r: number;
  cci: number;
  ema_cross: number;
  vwap: number;
}

export interface AnalysisPriceStats {
  last_price: number;
  pct_change_1d: number;
  pct_change_1m: number;
}

export interface AnalystConsensus {
  total_analysts: number;
  strong_buy: number;
  buy: number;
  hold: number;
  sell: number;
  strong_sell: number;
  consensus_rating: "Strong Buy" | "Buy" | "Hold" | "Sell" | "Strong Sell" | null;
  consensus_score: number | null;   // 1 (Strong Sell) … 5 (Strong Buy), blended from 3 sources
  target_price_avg: number | null;
  target_price_high: number | null;
  target_price_low: number | null;
  recent_upgrades?: number;
  recent_downgrades?: number;
}

export interface AnalysisResponse {
  ticker: string;
  rating: "Strong Buy" | "Buy" | "Hold" | "Sell" | "Strong Sell";
  composite_score: number;
  confidence_pct: number;
  reasoning: string[];
  indicators: AnalysisIndicators;
  signal_scores: AnalysisSignalScores;
  price_stats: AnalysisPriceStats;
  bar_count: number;
  as_of: string;
  analyst_consensus: AnalystConsensus | null;
}

// ---------------------------------------------------------------------------
// Portfolio metrics (from GET /api/portfolio/metrics)
// ---------------------------------------------------------------------------

export interface PortfolioMetricsResponse {
  total_return_pct: number;
  cagr_pct: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  calmar_ratio: number;
  max_drawdown_pct: number;
  annual_volatility_pct: number;
  n_trades: number;
  n_wins: number;
  n_losses: number;
  win_rate_pct: number;
  profit_factor: number;
  avg_trade_pnl: number;
  final_equity: number;
  initial_capital: number;
  total_pnl: number;
  start_date: string | null;
  end_date: string | null;
  n_calendar_days: number;
  strategy_attribution?: Record<string, number>;
}

// ---------------------------------------------------------------------------
// AI Analyst (from POST /api/ai/analyse)
// ---------------------------------------------------------------------------

export interface AiAnalyseRequest {
  tickers?: string[];
  include_trades?: boolean;
  include_news?: boolean;
  focus?: "full" | "risk" | "trades" | "market" | "outlook";
}

export interface AiAnalystReport {
  generated_at: string;
  provider: string;          // "openai" | "anthropic" | "offline"
  model: string;
  tickers: string[];
  focus: string;
  summary: string;
  market_commentary: string;
  trade_rationale: string;
  risk_assessment: string;
  outlook: string;
  key_points: string[];
  raw_response: string;
  context_snapshot: Record<string, unknown>;
}
