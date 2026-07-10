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


def _stochastic(highs: list[float], lows: list[float], closes: list[float], k_period: int = 14, d_period: int = 3) -> tuple[float, float]:
    """Returns (%K, %D)."""
    if len(closes) < k_period:
        return 50.0, 50.0
    window_h = highs[-k_period:]
    window_l = lows[-k_period:]
    highest_high = max(window_h)
    lowest_low = min(window_l)
    if highest_high == lowest_low:
        return 50.0, 50.0
    k = (closes[-1] - lowest_low) / (highest_high - lowest_low) * 100
    # %D = simple moving average of the last d_period %K values
    ks = []
    for i in range(d_period):
        idx = -(d_period - i)
        w_h = highs[max(0, idx - k_period):idx] if idx != 0 else highs[-k_period:]
        w_l = lows[max(0, idx - k_period):idx] if idx != 0 else lows[-k_period:]
        if not w_h or not w_l:
            ks.append(50.0)
            continue
        hh = max(w_h)
        ll = min(w_l)
        c = closes[idx - 1] if idx != 0 else closes[-1]
        ks.append((c - ll) / (hh - ll) * 100 if hh != ll else 50.0)
    ks.append(k)
    d = sum(ks[-d_period:]) / d_period
    return round(k, 2), round(d, 2)


def _williams_r(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Williams %R — ranges -100 (most oversold) to 0 (most overbought)."""
    if len(closes) < period:
        return -50.0
    window_h = highs[-period:]
    window_l = lows[-period:]
    highest_high = max(window_h)
    lowest_low = min(window_l)
    if highest_high == lowest_low:
        return -50.0
    return round((highest_high - closes[-1]) / (highest_high - lowest_low) * -100, 2)


def _obv(closes: list[float], volumes: list[float]) -> float:
    """On-Balance Volume — positive = accumulation, negative = distribution."""
    if len(closes) < 2:
        return 0.0
    obv = 0.0
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv += volumes[i]
        elif closes[i] < closes[i - 1]:
            obv -= volumes[i]
    return round(obv, 0)


def _vwap(highs: list[float], lows: list[float], closes: list[float], volumes: list[float], period: int = 20) -> float:
    """Rolling VWAP over the last *period* bars."""
    n = min(period, len(closes))
    if n == 0:
        return closes[-1] if closes else 0.0
    typical = [(highs[-n + i] + lows[-n + i] + closes[-n + i]) / 3 for i in range(n)]
    vols = volumes[-n:]
    total_vol = sum(vols)
    if total_vol == 0:
        return closes[-1]
    return round(sum(t * v for t, v in zip(typical, vols)) / total_vol, 4)


def _cci(highs: list[float], lows: list[float], closes: list[float], period: int = 20) -> float:
    """Commodity Channel Index."""
    if len(closes) < period:
        return 0.0
    typical = [(highs[-period + i] + lows[-period + i] + closes[-period + i]) / 3 for i in range(period)]
    mean_tp = sum(typical) / period
    mean_dev = sum(abs(t - mean_tp) for t in typical) / period
    if mean_dev < 1e-10:
        return 0.0
    return round((typical[-1] - mean_tp) / (0.015 * mean_dev), 2)


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
    closes  = [b["close"]  for b in bars]
    highs   = [b["high"]   for b in bars]
    lows    = [b["low"]    for b in bars]
    volumes = [b.get("volume", 0.0) for b in bars]

    rsi_val              = _rsi(closes)
    macd_line, sig_line  = _macd(closes)
    bb_upper, bb_mid, bb_lower = _bollinger(closes)
    sma20   = _sma(closes, 20)
    sma50   = _sma(closes, 50)
    sma200  = _sma(closes, 200)
    ema9    = _ema(closes, 9)[-1]
    ema21   = _ema(closes, 21)[-1]
    atr     = _atr(highs, lows, closes)
    stoch_k, stoch_d = _stochastic(highs, lows, closes)
    will_r  = _williams_r(highs, lows, closes)
    obv_val = _obv(closes, volumes)
    vwap_val = _vwap(highs, lows, closes, volumes)
    cci_val = _cci(highs, lows, closes)
    price   = closes[-1]

    # Volume analysis
    avg_vol_20 = sum(volumes[-20:]) / max(1, len(volumes[-20:]))
    current_vol = volumes[-1] if volumes else 0.0
    vol_ratio = round(current_vol / avg_vol_20, 2) if avg_vol_20 > 0 else 1.0

    # Individual signal scores [-1, +1]
    rsi_score   = _score_rsi(rsi_val)
    macd_score  = 1.0 if macd_line > sig_line else -1.0
    bb_score    = (-1.0 if price > bb_upper else (1.0 if price < bb_lower else 0.0))
    ma_score    = (1.0 if price > sma50 and sma50 > sma200 else
                   (-1.0 if price < sma50 and sma50 < sma200 else 0.0))
    trend_score = (1.0 if sma20 > sma50 else -1.0)
    # Stochastic: <20 = oversold (bullish), >80 = overbought (bearish)
    stoch_score = (0.6 if stoch_k < 20 and stoch_k > stoch_d else
                   -0.6 if stoch_k > 80 and stoch_k < stoch_d else 0.0)
    # Williams %R: < -80 = oversold, > -20 = overbought
    willr_score = (0.5 if will_r < -80 else -0.5 if will_r > -20 else 0.0)
    # CCI: >100 overbought, <-100 oversold
    cci_score   = (-0.5 if cci_val > 100 else 0.5 if cci_val < -100 else 0.0)
    # EMA crossover: fast EMA above slow = bullish
    ema_score   = (0.4 if ema9 > ema21 else -0.4)
    # VWAP: above VWAP = bullish momentum
    vwap_score  = (0.3 if price > vwap_val else -0.3)

    # Weighted composite (sum of weights = 1.0)
    composite = (
        rsi_score   * 0.14
        + macd_score  * 0.18
        + bb_score    * 0.10
        + ma_score    * 0.18
        + trend_score * 0.08
        + stoch_score * 0.10
        + willr_score * 0.07
        + cci_score   * 0.05
        + ema_score   * 0.06
        + vwap_score  * 0.04
    )

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

    if macd_line > sig_line:
        reasons.append(f"MACD bullish crossover (line {macd_line:.3f} > signal {sig_line:.3f})")
    else:
        reasons.append(f"MACD bearish (line {macd_line:.3f} < signal {sig_line:.3f})")

    if price > sma200:
        reasons.append(f"Price ${price:.2f} is above 200-day SMA ${sma200:.2f} — long-term uptrend")
    else:
        reasons.append(f"Price ${price:.2f} is below 200-day SMA ${sma200:.2f} — long-term downtrend")

    if price < bb_lower:
        reasons.append("Price is below lower Bollinger Band — statistically cheap vs. recent range")
    elif price > bb_upper:
        reasons.append("Price is above upper Bollinger Band — extended vs. recent range")

    if stoch_k < 20:
        reasons.append(f"Stochastic %K {stoch_k:.1f} — oversold, watch for bullish %K/%D crossover")
    elif stoch_k > 80:
        reasons.append(f"Stochastic %K {stoch_k:.1f} — overbought, watch for bearish reversal")

    if will_r < -80:
        reasons.append(f"Williams %R {will_r:.1f} — deeply oversold")
    elif will_r > -20:
        reasons.append(f"Williams %R {will_r:.1f} — overbought zone")

    if cci_val > 150:
        reasons.append(f"CCI {cci_val:.0f} — strongly overbought, high mean-reversion risk")
    elif cci_val < -150:
        reasons.append(f"CCI {cci_val:.0f} — strongly oversold, potential bounce setup")

    if vol_ratio > 1.5:
        reasons.append(f"Volume {vol_ratio:.1f}× avg — elevated conviction behind price move")
    elif vol_ratio < 0.5:
        reasons.append(f"Volume {vol_ratio:.1f}× avg — low participation, move may lack follow-through")

    pct_1d = ((closes[-1] - closes[-2]) / closes[-2] * 100) if len(closes) >= 2 else 0.0
    pct_1m = ((closes[-1] - closes[-22]) / closes[-22] * 100) if len(closes) >= 22 else 0.0

    return {
        "rating": rating,
        "composite_score": round(composite, 4),
        "confidence_pct": round(confidence * 100, 1),
        "reasoning": reasons,
        "indicators": {
            "rsi":          round(rsi_val, 2),
            "macd_line":    round(macd_line, 4),
            "macd_signal":  round(sig_line, 4),
            "bb_upper":     round(bb_upper, 2),
            "bb_mid":       round(bb_mid, 2),
            "bb_lower":     round(bb_lower, 2),
            "sma_20":       round(sma20, 2),
            "sma_50":       round(sma50, 2),
            "sma_200":      round(sma200, 2),
            "ema_9":        round(ema9, 2),
            "ema_21":       round(ema21, 2),
            "atr":          round(atr, 4),
            "stoch_k":      stoch_k,
            "stoch_d":      stoch_d,
            "williams_r":   will_r,
            "cci":          cci_val,
            "obv":          obv_val,
            "vwap_20":      round(vwap_val, 2),
            "volume_ratio": vol_ratio,
        },
        "signal_scores": {
            "rsi":         round(rsi_score, 3),
            "macd":        round(macd_score, 3),
            "bollinger":   round(bb_score, 3),
            "ma_trend":    round(ma_score, 3),
            "short_trend": round(trend_score, 3),
            "stochastic":  round(stoch_score, 3),
            "williams_r":  round(willr_score, 3),
            "cci":         round(cci_score, 3),
            "ema_cross":   round(ema_score, 3),
            "vwap":        round(vwap_score, 3),
        },
        "price_stats": {
            "last_price":    round(price, 4),
            "pct_change_1d": round(pct_1d, 2),
            "pct_change_1m": round(pct_1m, 2),
        },
    }


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Analyst consensus — fetched from yfinance recommendations summary
# ---------------------------------------------------------------------------

def _fetch_analyst_consensus(ticker: str) -> dict[str, Any]:
    """
    Fetch Wall Street analyst consensus ratings from yfinance.

    Returns a dict with:
        total_analysts   : int
        strong_buy       : int
        buy              : int
        hold             : int
        sell             : int
        strong_sell      : int
        consensus_rating : str  ("Strong Buy" | "Buy" | "Hold" | "Sell" | "Strong Sell")
        consensus_score  : float  (1=Strong Sell … 5=Strong Buy, matches Fidelity scale)
        target_price_avg : float | None
        target_price_high: float | None
        target_price_low : float | None
    """
    empty: dict[str, Any] = {
        "total_analysts": 0,
        "strong_buy": 0,
        "buy": 0,
        "hold": 0,
        "sell": 0,
        "strong_sell": 0,
        "consensus_rating": None,
        "consensus_score": None,
        "target_price_avg": None,
        "target_price_high": None,
        "target_price_low": None,
    }
    try:
        import yfinance as yf  # type: ignore[import]
        tkr = yf.Ticker(ticker)

        # ── Recommendation summary (strong_buy/buy/hold/sell/strong_sell counts) ──
        rec_summary = getattr(tkr, "recommendations_summary", None)
        if rec_summary is not None and hasattr(rec_summary, "empty") and not rec_summary.empty:
            # Most recent period is the first row
            row = rec_summary.iloc[0]
            sb  = int(row.get("strongBuy",   0))
            b   = int(row.get("buy",         0))
            h   = int(row.get("hold",        0))
            s   = int(row.get("sell",        0))
            ss  = int(row.get("strongSell",  0))
            total = sb + b + h + s + ss
            if total > 0:
                # Weighted score: SB=5, B=4, H=3, S=2, SS=1
                score = (sb * 5 + b * 4 + h * 3 + s * 2 + ss * 1) / total
                if score >= 4.5:
                    rating = "Strong Buy"
                elif score >= 3.7:
                    rating = "Buy"
                elif score >= 2.8:
                    rating = "Hold"
                elif score >= 2.0:
                    rating = "Sell"
                else:
                    rating = "Strong Sell"
                empty.update({
                    "total_analysts": total,
                    "strong_buy": sb,
                    "buy": b,
                    "hold": h,
                    "sell": s,
                    "strong_sell": ss,
                    "consensus_rating": rating,
                    "consensus_score": round(score, 2),
                })

        # ── Price targets ──────────────────────────────────────────────────────
        info = tkr.info or {}
        tp_avg  = info.get("targetMeanPrice")
        tp_high = info.get("targetHighPrice")
        tp_low  = info.get("targetLowPrice")
        if tp_avg:
            empty["target_price_avg"]  = round(float(tp_avg),  2)
        if tp_high:
            empty["target_price_high"] = round(float(tp_high), 2)
        if tp_low:
            empty["target_price_low"]  = round(float(tp_low),  2)

    except Exception as exc:
        logger.debug("analysis.analyst_consensus_failed ticker=%s error=%s", ticker, exc)

    return empty


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

    Also fetches Wall Street analyst consensus ratings (strong buy / buy /
    hold / sell / strong sell counts and price targets) from yfinance.
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
    loop = asyncio.get_event_loop()
    if len(bars_raw) < 50:
        try:
            from data.feeds.yfinance_feed import YFinanceFeed
            yf = YFinanceFeed()
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

    # 3. Fetch analyst consensus in parallel (best-effort, non-blocking)
    try:
        analyst = await loop.run_in_executor(None, _fetch_analyst_consensus, ticker)
        result["analyst_consensus"] = analyst
    except Exception as exc:
        logger.debug("analysis.analyst_consensus_skipped ticker=%s error=%s", ticker, exc)
        result["analyst_consensus"] = None

    return result
