"""
api/routes/analysis.py — On-demand ticker analysis endpoint.

GET /api/analysis/{ticker}
    Fetches the last 200 daily bars for *ticker* (DB first, then yfinance),
    computes a set of technical indicators, runs a lightweight signal scan
    (RSI, MACD, Bollinger, moving-average cross) and returns a composite
    rating, confidence %, individual indicator scores and a short reasoning
    string — all in a single JSON response suitable for the TickerAnalysis
    dashboard page.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.deps import AppState, get_app_state

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analysis", tags=["analysis"])


# ---------------------------------------------------------------------------
# Helpers — pure numpy / pandas computations (no ML models, fast)
# ---------------------------------------------------------------------------

def _ema(series: list[float], period: int) -> list[float]:
    result: list[float] = []
    k = 2 / (period + 1)
    for i, v in enumerate(series):
        if i == 0:
            result.append(v)
        else:
            result.append(v * k + result[-1] * (1 - k))
    return result


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss < 1e-10:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _macd(closes: list[float]) -> tuple[float, float]:
    """Returns (macd_line, signal_line)."""
    if len(closes) < 26:
        return 0.0, 0.0
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd = [a - b for a, b in zip(ema12, ema26)]
    signal = _ema(macd, 9)
    return macd[-1], signal[-1]


def _bollinger(closes: list[float], period: int = 20) -> tuple[float, float, float]:
    """Returns (upper, middle, lower)."""
    if len(closes) < period:
        c = closes[-1] if closes else 0.0
        return c, c, c
    window = closes[-period:]
    mid = sum(window) / period
    std = (sum((x - mid) ** 2 for x in window) / period) ** 0.5
    return mid + 2 * std, mid, mid - 2 * std


def _sma(closes: list[float], period: int) -> float:
    if len(closes) < period:
        return closes[-1] if closes else 0.0
    return sum(closes[-period:]) / period


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    if len(closes) < 2:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    return sum(trs[-period:]) / min(period, len(trs))


# ---------------------------------------------------------------------------
# Composite scorer — returns score in [-1, +1]
# ---------------------------------------------------------------------------

def _score_rsi(rsi: float) -> float:
    if rsi < 30:
        return 0.8
    if rsi < 40:
        return 0.4
    if rsi > 70:
        return -0.8
    if rsi > 60:
        return -0.4
    return 0.0


def _composite_analysis(bars: list[dict]) -> dict[str, Any]:
    closes  = [b["close"] for b in bars]
    highs   = [b["high"]  for b in bars]
    lows    = [b["low"]   for b in bars]

    rsi_val    = _rsi(closes)
    macd_line, signal_line = _macd(closes)
    bb_upper, bb_mid, bb_lower = _bollinger(closes)
    sma20  = _sma(closes, 20)
    sma50  = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    atr    = _atr(highs, lows, closes)
    price  = closes[-1]

    # Individual signal scores [-1, +1]
    rsi_score   = _score_rsi(rsi_val)
    macd_score  = 1.0 if macd_line > signal_line else -1.0
    bb_score    = (-1.0 if price > bb_upper else (1.0 if price < bb_lower else 0.0))
    ma_score    = (1.0 if price > sma50 and sma50 > sma200 else
                   (-1.0 if price < sma50 and sma50 < sma200 else 0.0))
    trend_score = (1.0 if sma20 > sma50 else -1.0)

    composite = (rsi_score * 0.20 + macd_score * 0.30 + bb_score * 0.15
                 + ma_score * 0.25 + trend_score * 0.10)

    # Map composite to rating
    if composite >= 0.45:
        rating = "Strong Buy"
    elif composite >= 0.15:
        rating = "Buy"
    elif composite <= -0.45:
        rating = "Strong Sell"
    elif composite <= -0.15:
        rating = "Sell"
    else:
        rating = "Hold"

    confidence = min(0.95, 0.50 + abs(composite) * 0.55)

    # Human-readable reasoning bullets
    reasons: list[str] = []
    if rsi_val < 30:
        reasons.append(f"RSI {rsi_val:.1f} — oversold territory, potential reversal upward")
    elif rsi_val > 70:
        reasons.append(f"RSI {rsi_val:.1f} — overbought, risk of near-term pullback")
    else:
        reasons.append(f"RSI {rsi_val:.1f} — neutral momentum")

    if macd_line > signal_line:
        reasons.append(f"MACD bullish crossover (line {macd_line:.3f} > signal {signal_line:.3f})")
    else:
        reasons.append(f"MACD bearish (line {macd_line:.3f} < signal {signal_line:.3f})")

    if price > sma200:
        reasons.append(f"Price ${price:.2f} is above 200-day SMA ${sma200:.2f} — long-term uptrend")
    else:
        reasons.append(f"Price ${price:.2f} is below 200-day SMA ${sma200:.2f} — long-term downtrend")

    if price < bb_lower:
        reasons.append("Price is below lower Bollinger Band — statistically cheap vs. recent range")
    elif price > bb_upper:
        reasons.append("Price is above upper Bollinger Band — extended vs. recent range")

    pct_1d = ((closes[-1] - closes[-2]) / closes[-2] * 100) if len(closes) >= 2 else 0.0
    pct_1m = ((closes[-1] - closes[-22]) / closes[-22] * 100) if len(closes) >= 22 else 0.0

    return {
        "rating": rating,
        "composite_score": round(composite, 4),
        "confidence_pct": round(confidence * 100, 1),
        "reasoning": reasons,
        "indicators": {
            "rsi":         round(rsi_val, 2),
            "macd_line":   round(macd_line, 4),
            "macd_signal": round(signal_line, 4),
            "bb_upper":    round(bb_upper, 2),
            "bb_mid":      round(bb_mid, 2),
            "bb_lower":    round(bb_lower, 2),
            "sma_20":      round(sma20, 2),
            "sma_50":      round(sma50, 2),
            "sma_200":     round(sma200, 2),
            "atr":         round(atr, 4),
        },
        "signal_scores": {
            "rsi":   round(rsi_score, 3),
            "macd":  round(macd_score, 3),
            "bollinger": round(bb_score, 3),
            "ma_trend":  round(ma_score, 3),
            "short_trend": round(trend_score, 3),
        },
        "price_stats": {
            "last_price":  round(price, 4),
            "pct_change_1d": round(pct_1d, 2),
            "pct_change_1m": round(pct_1m, 2),
        },
    }


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.get("/{ticker}")
async def get_analysis(
    ticker: str,
    state: AppState = Depends(get_app_state),
) -> dict:
    """
    Return a composite technical analysis for *ticker*.

    Fetches the last 200 daily bars from the DataStore (falling back to
    yfinance for tickers not in the pipeline universe), then computes
    RSI, MACD, Bollinger Bands, and moving-average signals to produce a
    **Strong Buy / Buy / Hold / Sell / Strong Sell** composite rating with
    a confidence % and per-indicator breakdown.
    """
    ticker = ticker.upper().strip()
    if not ticker:
        raise HTTPException(status_code=422, detail="ticker must not be empty")

    bars_raw: list[dict] = []
    end = datetime.now(UTC)
    start = end - timedelta(days=400)

    # 1. Try the DataStore first (fast, no network)
    store = state.data_store
    if store is not None:
        try:
            db_bars = store.read_bars(ticker=ticker, interval="1d", start=start, end=end)
            bars_raw = [
                {"close": b.close, "high": b.high, "low": b.low,
                 "open": b.open, "volume": b.volume}
                for b in db_bars
            ]
        except Exception as exc:
            logger.warning("analysis.store_read_failed ticker=%s error=%s", ticker, exc)

    # 2. Fall back to yfinance for arbitrary / non-pipeline tickers
    if len(bars_raw) < 50:
        try:
            from data.feeds.yfinance_feed import YFinanceFeed
            yf = YFinanceFeed()
            loop = asyncio.get_event_loop()
            yf_bars = await loop.run_in_executor(
                None, yf.fetch_bars, ticker, "1d", start, end
            )
            bars_raw = [
                {"close": b.close, "high": b.high, "low": b.low,
                 "open": b.open, "volume": b.volume}
                for b in yf_bars
            ]
        except Exception as exc:
            logger.warning("analysis.yfinance_failed ticker=%s error=%s", ticker, exc)

    if len(bars_raw) < 14:
        raise HTTPException(
            status_code=404,
            detail=f"Insufficient data for {ticker} — need at least 14 daily bars.",
        )

    result = _composite_analysis(bars_raw)
    result["ticker"] = ticker
    result["bar_count"] = len(bars_raw)
    result["as_of"] = end.isoformat()
    return result
