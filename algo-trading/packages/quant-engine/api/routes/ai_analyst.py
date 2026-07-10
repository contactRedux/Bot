"""
api/routes/ai_analyst.py — LLM-powered trading analyst.

POST /api/ai/analyse
    Gathers live data (technical indicators, portfolio state, recent trades,
    news headlines, risk metrics) and sends it to an LLM (OpenAI or Anthropic)
    which returns a structured analyst report: market commentary, trade
    rationale, risk warnings, and an actionable outlook.

GET /api/ai/history
    Returns the last N cached analyst reports (in-memory, newest first).

Configuration (.env)
--------------------
    LLM_PROVIDER=openai          # "openai" or "anthropic"
    LLM_API_KEY=sk-...           # your API key
    LLM_MODEL=gpt-4o             # model to use (defaults below)
    LLM_MAX_TOKENS=1200          # max response tokens

Supported models
----------------
    OpenAI   : gpt-4o (default), gpt-4o-mini, gpt-4-turbo
    Anthropic: claude-3-5-sonnet-20241022 (default), claude-3-haiku-20240307

If LLM_API_KEY is not set the endpoint returns a deterministic offline
summary built entirely from the platform's own computed data — useful in
dev mode without spending API credits.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import AppState, get_app_state

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ai", tags=["ai-analyst"])

# ---------------------------------------------------------------------------
# In-memory report history (max 50 entries, newest-first)
# ---------------------------------------------------------------------------
_report_history: list[dict] = []
_MAX_HISTORY = 50


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class AiAnalyseRequest(BaseModel):
    tickers: list[str] = Field(
        default=[],
        description="Specific tickers to focus on. Empty = use all active positions + watchlist.",
        examples=[["AAPL", "MSFT", "NVDA"]],
    )
    include_trades: bool = Field(
        default=True,
        description="Include recent trade history in the context sent to the LLM.",
    )
    include_news: bool = Field(
        default=True,
        description="Include recent news headlines in the context.",
    )
    focus: str = Field(
        default="full",
        description=(
            "What the analyst should focus on. "
            "Options: 'full' | 'risk' | 'trades' | 'market' | 'outlook'"
        ),
    )


class AiAnalystReport(BaseModel):
    generated_at: str
    provider: str                       # "openai" | "anthropic" | "offline"
    model: str
    tickers: list[str]
    focus: str
    summary: str                        # 1-paragraph executive summary
    market_commentary: str              # technical + macro context
    trade_rationale: str                # why the bot made the trades it did
    risk_assessment: str                # current risk posture
    outlook: str                        # forward-looking view
    key_points: list[str]               # bullet-point takeaways
    raw_response: str                   # full LLM response (for debugging)
    context_snapshot: dict[str, Any]    # data fed into the LLM (for transparency)


# ---------------------------------------------------------------------------
# Data gathering — assembles the context payload from live app state
# ---------------------------------------------------------------------------

def _gather_context(
    state: AppState,
    tickers: list[str],
    include_trades: bool,
    include_news: bool,
) -> dict[str, Any]:
    """
    Pull live data from AppState into a flat dict that will be serialised
    into the LLM prompt.  All values are kept small/summarised.
    """
    ctx: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "trading_mode": state.trading_mode,
    }

    # ── Portfolio ──────────────────────────────────────────────────────────
    pf = state.portfolio
    if pf is not None:
        snapshot = pf.positions_snapshot() if hasattr(pf, "positions_snapshot") else {}
        positions = []
        for tkr, pos in (snapshot or {}).items():
            qty = float(pos.get("quantity", 0.0))
            if abs(qty) < 1e-8:
                continue
            avg = float(pos.get("avg_cost", 0.0))
            mark = float(pos.get("mark_price", 0.0))
            upnl = (mark - avg) * qty if avg > 0 else 0.0
            positions.append({
                "ticker": tkr,
                "quantity": round(qty, 4),
                "avg_cost": round(avg, 2),
                "mark_price": round(mark, 2),
                "unrealised_pnl": round(upnl, 2),
            })
        ctx["portfolio"] = {
            "cash": round(float(getattr(pf, "cash", 0.0)), 2),
            "initial_capital": round(float(getattr(pf, "initial_capital", 100_000.0)), 2),
            "total_equity": round(
                float(getattr(pf, "cash", 0.0)) + sum(p["mark_price"] * p["quantity"] for p in positions), 2
            ),
            "realised_pnl": round(float(getattr(pf, "realised_pnl", 0.0)), 2),
            "open_positions": positions,
        }
    else:
        ctx["portfolio"] = None

    # ── Latest signals ─────────────────────────────────────────────────────
    ctx["latest_signals"] = [
        {k: v for k, v in s.items() if k != "raw"}
        for s in (state.latest_signals or [])[-20:]
    ]

    # ── Recent trades ──────────────────────────────────────────────────────
    if include_trades:
        broker = state.broker
        fills: list[dict] = []
        if broker is not None and hasattr(broker, "fills"):
            for fill in broker.fills[-15:]:
                d = fill.to_dict() if hasattr(fill, "to_dict") else vars(fill)
                fills.append({k: v for k, v in d.items()
                               if k in ("ticker", "side", "quantity", "fill_price",
                                        "commission", "strategy_id", "timestamp")})
        ctx["recent_trades"] = fills
    else:
        ctx["recent_trades"] = []

    # ── Risk metrics ───────────────────────────────────────────────────────
    monitor = state.monitor
    if monitor is not None:
        ctx["risk"] = {
            "halted": getattr(monitor, "halted", False),
            "halt_reason": getattr(monitor, "halt_reason", ""),
            "current_drawdown_pct": round(float(getattr(monitor, "current_drawdown_pct", 0.0)), 3),
            "peak_equity": round(float(getattr(monitor, "peak_equity", 0.0)), 2),
            "max_drawdown_limit_pct": round(float(getattr(monitor, "_limits", None) and
                                                   getattr(monitor._limits, "max_drawdown_pct", 0.20) or 0.20), 2),
        }
    else:
        ctx["risk"] = None

    # ── Technical analysis for each ticker ────────────────────────────────
    if tickers and state.data_store is not None:
        try:
            from api.routes.analysis import _composite_analysis
            end = datetime.now(UTC)
            start = end - timedelta(days=400)
            tech: dict[str, Any] = {}
            for tkr in tickers[:6]:  # cap at 6 to keep prompt size reasonable
                try:
                    bars = state.data_store.read_bars(ticker=tkr, interval="1d", start=start, end=end)
                    if len(bars) >= 14:
                        bar_dicts = [
                            {"close": b.close, "high": b.high, "low": b.low,
                             "open": b.open, "volume": b.volume}
                            for b in bars
                        ]
                        result = _composite_analysis(bar_dicts)
                        tech[tkr] = {
                            "rating": result["rating"],
                            "composite_score": result["composite_score"],
                            "confidence_pct": result["confidence_pct"],
                            "rsi": result["indicators"]["rsi"],
                            "macd_bullish": result["signal_scores"]["macd"] > 0,
                            "price": result["price_stats"]["last_price"],
                            "pct_1d": result["price_stats"]["pct_change_1d"],
                            "pct_1m": result["price_stats"]["pct_change_1m"],
                            "above_sma200": result["indicators"]["sma_200"] < result["price_stats"]["last_price"],
                        }
                except Exception:
                    pass
            ctx["technical_analysis"] = tech
        except Exception as exc:
            logger.debug("ai_analyst.tech_analysis_failed error=%s", exc)
            ctx["technical_analysis"] = {}
    else:
        ctx["technical_analysis"] = {}

    # ── Recent news headlines ──────────────────────────────────────────────
    if include_news and state.data_store is not None:
        try:
            store = state.data_store
            since = datetime.now(UTC) - timedelta(hours=48)
            raw_articles = store.read_news(since=since, limit=20)
            ctx["news_headlines"] = [
                {
                    "ticker": getattr(a, "ticker", ""),
                    "headline": getattr(a, "headline", ""),
                    "sentiment": getattr(a, "sentiment_label", "neutral"),
                    "score": round(float(getattr(a, "sentiment_score", 0.0)), 3),
                    "source": getattr(a, "source", ""),
                    "published_at": str(getattr(a, "published_at", "")),
                }
                for a in raw_articles
            ]
        except Exception as exc:
            logger.debug("ai_analyst.news_fetch_failed error=%s", exc)
            ctx["news_headlines"] = []
    else:
        ctx["news_headlines"] = []

    return ctx


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_prompt(ctx: dict[str, Any], tickers: list[str], focus: str) -> str:
    """Serialise context into a structured prompt for the LLM."""
    import json

    ticker_str = ", ".join(tickers) if tickers else "all active positions"
    focus_instructions = {
        "full":    "Provide a comprehensive analysis covering all sections.",
        "risk":    "Focus especially on risk metrics, drawdown, and position sizing concerns.",
        "trades":  "Focus especially on the rationale behind each recent trade and whether it was well-executed.",
        "market":  "Focus especially on market conditions and how the technical indicators compare across tickers.",
        "outlook": "Focus especially on the forward-looking trading outlook and recommended adjustments.",
    }.get(focus, "Provide a comprehensive analysis covering all sections.")

    # Serialise context cleanly, omitting None values
    context_json = json.dumps(
        {k: v for k, v in ctx.items() if v is not None},
        indent=2,
        default=str,
    )

    return f"""You are an expert quantitative trading analyst reviewing an algorithmic trading system.
The system runs multiple automated strategies (momentum, mean reversion, Kalman trend filter, VWAP reversion, Kelly volatility targeting, sentiment, macro factor, statistical arbitrage, and market making) on US equities and crypto.

{focus_instructions}

Your audience is the operator of this trading system. Be direct, precise, and actionable. Use financial terminology correctly. Do not hedge every statement with excessive disclaimers — one brief disclaimer at the end is sufficient.

Tickers in focus: {ticker_str}

---
LIVE SYSTEM DATA (as of {ctx.get("timestamp", "now")}):
{context_json}
---

Respond in this exact JSON format (no markdown fences, raw JSON only):
{{
  "summary": "<1-2 sentence executive summary of the overall system state>",
  "market_commentary": "<2-3 sentences on current market conditions based on technicals, RSI levels, MACD, and price vs SMA-200>",
  "trade_rationale": "<2-3 sentences explaining why the bot made the specific trades it did, referencing strategy names and signal values>",
  "risk_assessment": "<2-3 sentences on current risk posture: drawdown, position concentration, halted state if any>",
  "outlook": "<2-3 sentences forward-looking: what conditions would trigger more trades, what to watch>",
  "key_points": ["<bullet 1>", "<bullet 2>", "<bullet 3>", "<bullet 4>", "<bullet 5>"]
}}"""


# ---------------------------------------------------------------------------
# Offline fallback — generates a report without any LLM call
# ---------------------------------------------------------------------------

def _offline_report(ctx: dict[str, Any], tickers: list[str], focus: str) -> dict[str, str | list]:
    """
    Build a plain-text report entirely from computed data when no API key
    is configured.  Gives a useful read without spending LLM credits.
    """
    pf = ctx.get("portfolio") or {}
    risk = ctx.get("risk") or {}
    tech = ctx.get("technical_analysis") or {}
    signals = ctx.get("latest_signals") or []
    trades = ctx.get("recent_trades") or []
    news = ctx.get("news_headlines") or []

    equity = pf.get("total_equity", 0.0)
    initial = pf.get("initial_capital", 100_000.0)
    ret_pct = ((equity - initial) / initial * 100) if initial > 0 else 0.0
    n_positions = len(pf.get("open_positions", []))
    halted = risk.get("halted", False)
    dd = risk.get("current_drawdown_pct", 0.0)

    # Market commentary from tech analysis
    ratings = [(t, d["rating"], d["rsi"]) for t, d in tech.items()]
    bullish = [t for t, r, _ in ratings if "Buy" in r]
    bearish = [t for t, r, _ in ratings if "Sell" in r]
    avg_rsi = sum(d["rsi"] for d in tech.values()) / len(tech) if tech else 50.0

    # Trade rationale from recent signals
    active_strategies = list({s.get("strategy_id", "") for s in signals if s.get("signal", 0) != 0})

    # Sentiment from news
    pos_news = [n for n in news if n.get("sentiment") == "positive"]
    neg_news = [n for n in news if n.get("sentiment") == "negative"]

    summary = (
        f"System is {'HALTED — risk limits breached' if halted else 'running'}. "
        f"Portfolio equity ${equity:,.2f} ({ret_pct:+.2f}% vs initial), "
        f"{n_positions} open position{'s' if n_positions != 1 else ''}. "
        f"Current drawdown: {dd:.2f}%."
    )

    market_commentary = (
        f"Average RSI across tracked tickers is {avg_rsi:.1f} "
        f"({'oversold territory — potential mean-reversion opportunities' if avg_rsi < 35 else 'overbought — watch for reversals' if avg_rsi > 65 else 'neutral momentum zone'}). "
        f"Bullish technical ratings: {', '.join(bullish) or 'none'}. "
        f"Bearish technical ratings: {', '.join(bearish) or 'none'}."
    )

    trade_rationale = (
        f"Recent activity driven by {', '.join(active_strategies) or 'no active strategies'}. "
        f"{len(trades)} trade{'s' if len(trades) != 1 else ''} executed in the current session. "
        f"Strategies fire when price deviates from statistical norms (mean reversion), trend indicators align (momentum/Kalman), or sentiment scores cross thresholds."
    )

    risk_assessment = (
        f"Drawdown at {dd:.2f}% — {'within limits' if dd < 15 else 'approaching limit — consider reducing exposure'}. "
        f"{'SYSTEM HALTED: ' + risk.get('halt_reason', '') if halted else 'No halt conditions active.'}. "
        f"Risk monitor is {'active' if risk else 'not initialised'}."
    )

    outlook = (
        f"Sentiment news tilt: {len(pos_news)} positive vs {len(neg_news)} negative headlines in last 48h. "
        f"{'Mean reversion setups likely if RSI continues declining.' if avg_rsi > 60 else 'Momentum conditions improving if RSI recovers above 50.' if avg_rsi < 45 else 'Neutral environment — mixed signal quality expected.'}. "
        f"Watch for macro regime changes that could trigger the MacroFactor strategy's VIX or yield-curve overlays."
    )

    key_points = [
        f"Total return vs initial capital: {ret_pct:+.2f}%",
        f"Open positions: {n_positions} | Active strategies: {len(active_strategies)}",
        f"RSI average: {avg_rsi:.1f} — {'oversold' if avg_rsi < 35 else 'overbought' if avg_rsi > 65 else 'neutral'}",
        f"Drawdown: {dd:.2f}% | System halted: {halted}",
        f"News sentiment: {len(pos_news)} positive / {len(neg_news)} negative headlines (48h)",
    ]

    return {
        "summary": summary,
        "market_commentary": market_commentary,
        "trade_rationale": trade_rationale,
        "risk_assessment": risk_assessment,
        "outlook": outlook,
        "key_points": key_points,
    }


# ---------------------------------------------------------------------------
# LLM caller
# ---------------------------------------------------------------------------

async def _call_llm(
    prompt: str,
    provider: str,
    api_key: str,
    model: str,
    max_tokens: int,
) -> str:
    """Call the LLM and return the raw response string."""
    loop = asyncio.get_event_loop()

    if provider == "openai":
        def _openai_call() -> str:
            import openai  # type: ignore[import]
            client = openai.OpenAI(api_key=api_key)
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an expert quantitative trading analyst. "
                            "Always respond in valid JSON only — no markdown, no fences."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=max_tokens,
                temperature=0.3,
                response_format={"type": "json_object"},
            )
            return resp.choices[0].message.content or ""

        return await loop.run_in_executor(None, _openai_call)

    elif provider == "anthropic":
        def _anthropic_call() -> str:
            import anthropic  # type: ignore[import]
            client = anthropic.Anthropic(api_key=api_key)
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=(
                    "You are an expert quantitative trading analyst. "
                    "Always respond in valid JSON only — no markdown, no fences."
                ),
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.content[0].text if resp.content else ""

        return await loop.run_in_executor(None, _anthropic_call)

    raise ValueError(f"Unknown provider: {provider!r}")


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def _parse_llm_response(raw: str) -> dict[str, Any]:
    """Parse the LLM JSON response, falling back to best-effort extraction."""
    import json
    import re

    # Strip any accidental markdown fences
    cleaned = re.sub(r"```(?:json)?", "", raw).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to pull out individual fields with regex as last resort
        logger.warning("ai_analyst.parse_failed — raw response was not valid JSON")
        return {
            "summary": raw[:500],
            "market_commentary": "",
            "trade_rationale": "",
            "risk_assessment": "",
            "outlook": "",
            "key_points": [],
        }


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

@router.post("/analyse", response_model=AiAnalystReport)
async def run_ai_analysis(
    body: AiAnalyseRequest,
    state: AppState = Depends(get_app_state),
) -> AiAnalystReport:
    """
    Generate a natural-language trading analyst report from live system data.

    Gathers technical indicators, portfolio state, recent trades, risk
    metrics, and news headlines, then sends them to an LLM (OpenAI or
    Anthropic) which produces a structured report.

    If ``LLM_API_KEY`` is not configured, returns an offline summary built
    entirely from the platform's own computed data.
    """
    # ── Config from env ────────────────────────────────────────────────────
    provider  = os.getenv("LLM_PROVIDER", "openai").lower()
    api_key   = os.getenv("LLM_API_KEY", "")
    max_tok   = int(os.getenv("LLM_MAX_TOKENS", "1200"))

    _default_models = {
        "openai":    "gpt-4o",
        "anthropic": "claude-3-5-sonnet-20241022",
    }
    model = os.getenv("LLM_MODEL", _default_models.get(provider, "gpt-4o"))

    # ── Resolve tickers ────────────────────────────────────────────────────
    tickers = [t.upper().strip() for t in body.tickers if t.strip()]
    if not tickers:
        # Fall back to active positions + orchestrator tickers
        pf = state.portfolio
        if pf is not None:
            snap = pf.positions_snapshot() if hasattr(pf, "positions_snapshot") else {}
            tickers = [t for t, pos in (snap or {}).items()
                       if abs(float(pos.get("quantity", 0))) > 1e-8]
        if not tickers and state.orchestrator is not None:
            for s in getattr(state.orchestrator, "strategies", []):
                for t in getattr(s, "tickers", []):
                    if t not in tickers:
                        tickers.append(t)
        if not tickers:
            tickers = ["AAPL", "MSFT", "NVDA"]

    # ── Gather context ─────────────────────────────────────────────────────
    ctx = _gather_context(state, tickers, body.include_trades, body.include_news)

    # ── Generate report ────────────────────────────────────────────────────
    raw_response = ""
    actual_provider = "offline"

    if api_key:
        try:
            prompt = _build_prompt(ctx, tickers, body.focus)
            raw_response = await _call_llm(prompt, provider, api_key, model, max_tok)
            parsed = _parse_llm_response(raw_response)
            actual_provider = provider
        except Exception as exc:
            logger.warning("ai_analyst.llm_call_failed error=%s — falling back to offline", exc)
            parsed = _offline_report(ctx, tickers, body.focus)
            raw_response = f"[LLM call failed: {exc}]"
            actual_provider = "offline"
    else:
        logger.info("ai_analyst.offline_mode no LLM_API_KEY configured")
        parsed = _offline_report(ctx, tickers, body.focus)
        actual_provider = "offline"
        model = "offline"

    generated_at = datetime.now(UTC).isoformat()
    report = AiAnalystReport(
        generated_at=generated_at,
        provider=actual_provider,
        model=model,
        tickers=tickers,
        focus=body.focus,
        summary=parsed.get("summary", ""),
        market_commentary=parsed.get("market_commentary", ""),
        trade_rationale=parsed.get("trade_rationale", ""),
        risk_assessment=parsed.get("risk_assessment", ""),
        outlook=parsed.get("outlook", ""),
        key_points=parsed.get("key_points", []),
        raw_response=raw_response,
        context_snapshot=ctx,
    )

    # ── Cache ──────────────────────────────────────────────────────────────
    _report_history.insert(0, report.model_dump())
    if len(_report_history) > _MAX_HISTORY:
        _report_history.pop()

    return report


@router.get("/history")
async def get_ai_history(
    limit: int = 10,
) -> dict:
    """Return the last N analyst reports (newest first)."""
    return {
        "reports": _report_history[:limit],
        "count": len(_report_history[:limit]),
    }
