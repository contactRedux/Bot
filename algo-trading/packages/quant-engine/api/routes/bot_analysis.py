"""
api/routes/bot_analysis.py — Bot Analysis endpoint.

GET /api/bot/watchlist
    Returns per-ticker analysis for every ticker the trading engine is currently
    watching.  For each ticker we return:
        - current price + 1-day / 1-month % change
        - technical composite rating (reuses _composite_analysis from analysis.py)
        - last signal emitted by the strategy engine (direction + confidence)
        - Wall Street analyst consensus if available (from yfinance)
        - position status (flat / long / short)
        - estimated upside to analyst target price
        - per-indicator signal scores (for the expandable detail row)

The response powers the Bot Analysis page in the dashboard.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends

from api.deps import AppState, get_app_state
from api.routes.analysis import _composite_analysis, _fetch_analyst_consensus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bot", tags=["bot"])


@router.get("/watchlist")
async def get_bot_watchlist(
    state: AppState = Depends(get_app_state),
) -> dict:
    """
    Return a per-ticker summary for every ticker the engine is monitoring.

    Iterates over the trading engine's ticker universe, pulls bars from the
    DataStore (falling back to yfinance), computes the same composite technical
    rating used by the Analysis page, and enriches with live engine signal +
    position data.  Runs all tickers concurrently.
    """
    engine = getattr(state, "trading_engine", None)
    if engine is None:
        return {"tickers": [], "count": 0, "engine_running": False,
                "loop_count": 0, "as_of": datetime.now(UTC).isoformat()}

    tickers: list[str] = list(getattr(engine, "tickers", []))
    if not tickers:
        return {"tickers": [], "count": 0, "engine_running": False,
                "loop_count": 0, "as_of": datetime.now(UTC).isoformat()}

    # Most recent signal per ticker (latest_signals is newest-last)
    latest_signals: dict[str, dict] = {}
    for sig in reversed(getattr(state, "latest_signals", [])):
        t = sig.get("ticker", "")
        if t and t not in latest_signals:
            latest_signals[t] = sig

    # Current positions keyed by ticker symbol
    portfolio = getattr(state, "portfolio", None)
    positions: dict[str, float] = {}
    if portfolio is not None:
        for pos in getattr(portfolio, "positions", {}).values():
            ticker_sym = getattr(pos, "ticker", "")
            qty = float(getattr(pos, "quantity", 0.0))
            if ticker_sym:
                positions[ticker_sym] = qty

    store = state.data_store
    end = datetime.now(UTC)
    start = end - timedelta(days=400)
    loop = asyncio.get_event_loop()

    async def _analyse_ticker(ticker: str) -> dict[str, Any] | None:
        bars_raw: list[dict] = []

        # 1. DataStore (fast, no network)
        if store is not None:
            try:
                db_bars = store.read_bars(ticker=ticker, interval="1d", start=start, end=end)
                bars_raw = [
                    {"close": b.close, "high": b.high, "low": b.low,
                     "open": b.open, "volume": b.volume}
                    for b in db_bars
                ]
            except Exception:
                pass

        # 2. yfinance fallback for tickers not yet in the DB
        if len(bars_raw) < 50:
            try:
                from data.feeds.yfinance_feed import YFinanceFeed
                yf = YFinanceFeed()
                yf_bars = await loop.run_in_executor(
                    None, yf.fetch_bars, ticker, "1d", start, end
                )
                if yf_bars:
                    bars_raw = [
                        {"close": b.close, "high": b.high, "low": b.low,
                         "open": b.open, "volume": b.volume}
                        for b in yf_bars
                    ]
            except Exception:
                pass

        if len(bars_raw) < 10:
            return None

        try:
            analysis = _composite_analysis(bars_raw)
        except Exception as exc:
            logger.debug("bot_watchlist.analysis_failed ticker=%s error=%s", ticker, exc)
            return None

        # Analyst consensus — blocking yfinance call wrapped in executor
        try:
            consensus = await loop.run_in_executor(None, _fetch_analyst_consensus, ticker)
        except Exception:
            consensus = None

        # Position status
        qty = positions.get(ticker, 0.0)
        position_status = "long" if qty > 0 else "short" if qty < 0 else "flat"

        # Latest engine signal for this ticker
        sig = latest_signals.get(ticker)

        # Upside % to analyst price target
        price = analysis["price_stats"]["last_price"]
        upside_pct: float | None = None
        if consensus and consensus.get("target_price_avg") and price > 0:
            upside_pct = round(
                (consensus["target_price_avg"] - price) / price * 100, 2
            )

        return {
            "ticker": ticker,
            "price": price,
            "pct_change_1d": analysis["price_stats"]["pct_change_1d"],
            "pct_change_1m": analysis["price_stats"]["pct_change_1m"],
            "technical_rating": analysis["rating"],
            "technical_score": analysis["composite_score"],
            "confidence_pct": analysis["confidence_pct"],
            "position_status": position_status,
            "position_qty": qty,
            "last_signal": {
                "direction": (
                    "buy" if sig.get("signal", 0) > 0
                    else "sell" if sig.get("signal", 0) < 0
                    else None
                ),
                "confidence": sig.get("confidence"),
                "timestamp": sig.get("timestamp"),
                "strategy_id": sig.get("strategy_id"),
            } if sig else None,
            "analyst_consensus": consensus,
            "upside_to_target_pct": upside_pct,
            "signal_scores": analysis["signal_scores"],
        }

    # Fan out all ticker lookups concurrently
    raw_results = await asyncio.gather(*[_analyse_ticker(t) for t in tickers])

    results = [item for item in raw_results if item is not None]

    # Sort: Strong Buy first, then by composite_score descending within each rating
    _order = {"Strong Buy": 0, "Buy": 1, "Hold": 2, "Sell": 3, "Strong Sell": 4}
    results.sort(key=lambda x: (
        _order.get(x.get("technical_rating", "Hold"), 2),
        -x.get("technical_score", 0),
    ))

    return {
        "tickers": results,
        "count": len(results),
        "engine_running": getattr(engine, "is_running", False),
        "loop_count": getattr(engine, "loop_count", 0),
        "as_of": datetime.now(UTC).isoformat(),
    }
