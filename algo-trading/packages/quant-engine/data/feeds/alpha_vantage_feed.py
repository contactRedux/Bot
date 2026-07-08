"""
data/feeds/alpha_vantage_feed.py — Fundamental data via Alpha Vantage API.

Alpha Vantage provides free access to fundamental financial data for US equities:
* Earnings (EPS, consensus estimates, surprise)
* Income statement (revenue, gross profit, operating income, net income)
* Balance sheet (debt-to-equity, book value)
* Overview (P/E ratio, P/B ratio, EV/EBITDA, ROE)

These are the inputs to the ``FundamentalSnapshot`` schema and power the
Macro Factor strategy (Sub-Task 5) and the fundamental feature module (Sub-Task 3).

Alpha Vantage API overview
--------------------------
Base URL: ``https://www.alphavantage.co/query``

Key functions used:
* ``OVERVIEW``          — real-time company overview with P/E, P/B, EV/EBITDA, ROE
* ``EARNINGS``          — quarterly/annual EPS with analyst consensus estimates
* ``INCOME_STATEMENT``  — quarterly/annual revenue, gross profit, operating income
* ``BALANCE_SHEET``     — debt-to-equity ratio

Rate limits (free tier):
* 5 API calls/minute
* 500 API calls/day

We enforce these limits via a configurable inter-request delay (default 12s to
stay well under the 5/min limit).  For production use, the paid tier (75+ calls/
min) is recommended.

Earnings surprise calculation
------------------------------
``eps_surprise = (reported_eps − consensus_eps) / |consensus_eps|``

This is computed automatically in ``FundamentalSnapshot.model_post_init`` when
both EPS fields are set.  The Alpha Vantage ``EARNINGS`` endpoint provides both
``reportedEPS`` (actual) and ``estimatedEPS`` (analyst consensus), making this
straightforward.

An earnings surprise > +2σ (where σ is estimated from trailing surprise
standard deviation) triggers a momentum signal in the Macro Factor strategy.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from data.feeds.base import DataFeed
from data.schemas import FundamentalSnapshot

logger = structlog.get_logger(__name__)

_AV_BASE_URL = "https://www.alphavantage.co/query"


def _safe_float(value: Any) -> float | None:
    """Convert a string or number to float, returning None on failure."""
    if value is None or value == "None" or value == "-":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_date(date_str: str | None) -> datetime | None:
    """Parse Alpha Vantage date strings like ``'2024-01-31'`` to UTC datetime."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class AlphaVantageFeed(DataFeed):
    """
    Fundamental data feed backed by the Alpha Vantage REST API.

    Parameters
    ----------
    config : dict, optional
        Required keys:
        - ``"api_key"`` : Alpha Vantage API key (read from settings by default)
        Optional keys:
        - ``"request_delay"`` : float seconds between requests (default 12.0)

    Example
    -------
    ::

        from data.feeds.alpha_vantage_feed import AlphaVantageFeed

        feed = AlphaVantageFeed(config={"api_key": "your-key"})
        snapshots = feed.fetch_fundamentals("AAPL", period="quarterly")
        for s in snapshots[:2]:
            print(s.ticker, s.period_end_date, s.eps_surprise)
    """

    SOURCE = "alpha_vantage"

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._api_key: str | None = self.config.get("api_key")
        self._request_delay: float = self.config.get("request_delay", 12.0)
        self._http_client = httpx.Client(timeout=30.0)

    def _get(self, function: str, ticker: str, **extra_params: Any) -> dict:
        """Make a single Alpha Vantage API request and return the JSON response."""
        if not self._api_key:
            raise ValueError(
                "Alpha Vantage API key is required.  Set ALPHA_VANTAGE_KEY in .env "
                "or pass config={'api_key': 'your-key'}."
            )
        params = {
            "function": function,
            "symbol": ticker,
            "apikey": self._api_key,
            **extra_params,
        }
        response = self._http_client.get(_AV_BASE_URL, params=params)
        response.raise_for_status()
        data = response.json()

        # Alpha Vantage returns an "Information" key when rate-limited
        if "Information" in data:
            raise RuntimeError(
                f"Alpha Vantage rate limit hit: {data['Information']}"
            )
        if "Error Message" in data:
            raise ValueError(f"Alpha Vantage error for {ticker}: {data['Error Message']}")

        return data

    def fetch_fundamentals(
        self,
        ticker: str,
        period: str = "quarterly",
    ) -> list[FundamentalSnapshot]:
        """
        Fetch fundamental data for a US equity ticker.

        Combines data from three Alpha Vantage endpoints:
        1. ``OVERVIEW``         — valuation ratios (P/E, P/B, ROE, etc.)
        2. ``EARNINGS``         — EPS, consensus estimate, surprise per period
        3. ``INCOME_STATEMENT`` — revenue, gross profit per period

        The results are joined on ``fiscalDateEnding`` (period end date) to
        produce one ``FundamentalSnapshot`` per reporting period.

        Parameters
        ----------
        ticker :
            US equity ticker symbol (e.g. ``"AAPL"``, ``"MSFT"``).
        period :
            ``"quarterly"`` or ``"annual"``.

        Returns
        -------
        list[FundamentalSnapshot]
            Sorted descending by ``period_end_date`` (most recent first).
        """
        logger.info("alpha_vantage.fetch_fundamentals", ticker=ticker, period=period)

        # Step 1: Fetch company overview (current-period valuation ratios)
        overview = self._fetch_overview(ticker)
        time.sleep(self._request_delay)

        # Step 2: Fetch EPS history with consensus estimates
        earnings = self._fetch_earnings(ticker, period)
        time.sleep(self._request_delay)

        # Step 3: Fetch income statement history
        income = self._fetch_income_statement(ticker, period)
        time.sleep(self._request_delay)

        # Build a mapping from fiscal date → income statement row
        income_by_date: dict[str, dict] = {
            row.get("fiscalDateEnding", ""): row
            for row in income
        }

        snapshots: list[FundamentalSnapshot] = []
        fetch_ts = datetime.now(tz=timezone.utc)

        for earnings_row in earnings:
            try:
                fiscal_date_str = earnings_row.get("fiscalDateEnding", "")
                period_end = _parse_date(fiscal_date_str)
                if period_end is None:
                    continue

                # Report date: when the earnings became public
                report_date_str = earnings_row.get("reportedDate", fiscal_date_str)
                report_date = _parse_date(report_date_str) or period_end

                # Income statement data for this period
                inc = income_by_date.get(fiscal_date_str, {})

                # Use most-recent overview P/E for latest period only;
                # historical P/E ratios are not available from free AV tier.
                is_latest = fiscal_date_str == earnings[0].get("fiscalDateEnding", "")
                pe_ratio = _safe_float(overview.get("PERatio")) if is_latest else None
                pb_ratio = _safe_float(overview.get("PriceToBookRatio")) if is_latest else None
                ev_ebitda = _safe_float(overview.get("EVToEBITDA")) if is_latest else None
                debt_to_equity = _safe_float(overview.get("DebtToEquityRatio")) if is_latest else None
                roe = _safe_float(overview.get("ReturnOnEquityTTM")) if is_latest else None

                snapshot = FundamentalSnapshot(
                    ticker=ticker,
                    period=period,  # type: ignore[arg-type]
                    period_end_date=period_end,
                    report_date=report_date,
                    revenue=_safe_float(inc.get("totalRevenue")),
                    gross_profit=_safe_float(inc.get("grossProfit")),
                    operating_income=_safe_float(inc.get("operatingIncome")),
                    net_income=_safe_float(inc.get("netIncome")),
                    eps_reported=_safe_float(earnings_row.get("reportedEPS")),
                    eps_consensus=_safe_float(earnings_row.get("estimatedEPS")),
                    # eps_surprise is computed automatically in model_post_init
                    pe_ratio=pe_ratio,
                    pb_ratio=pb_ratio,
                    ev_ebitda=ev_ebitda,
                    debt_to_equity=debt_to_equity,
                    return_on_equity=roe,
                    currency="USD",
                    source=self.SOURCE,
                    event_timestamp=report_date,
                    fetch_timestamp=fetch_ts,
                )
                snapshots.append(snapshot)
            except Exception as exc:
                logger.warning(
                    "alpha_vantage.snapshot_parse_error",
                    ticker=ticker,
                    error=str(exc),
                )

        logger.info(
            "alpha_vantage.fetch_fundamentals.done",
            ticker=ticker,
            snapshots_returned=len(snapshots),
        )
        return snapshots

    def _fetch_overview(self, ticker: str) -> dict:
        """Fetch company overview (current-period valuation ratios)."""
        try:
            return self._get("OVERVIEW", ticker)
        except Exception as exc:
            logger.warning("alpha_vantage.overview_error", ticker=ticker, error=str(exc))
            return {}

    def _fetch_earnings(self, ticker: str, period: str) -> list[dict]:
        """Fetch EPS history with analyst consensus estimates."""
        try:
            data = self._get("EARNINGS", ticker)
            key = "quarterlyEarnings" if period == "quarterly" else "annualEarnings"
            return data.get(key, [])
        except Exception as exc:
            logger.warning("alpha_vantage.earnings_error", ticker=ticker, error=str(exc))
            return []

    def _fetch_income_statement(self, ticker: str, period: str) -> list[dict]:
        """Fetch income statement history (revenue, gross profit, etc.)."""
        try:
            data = self._get("INCOME_STATEMENT", ticker)
            key = "quarterlyReports" if period == "quarterly" else "annualReports"
            return data.get(key, [])
        except Exception as exc:
            logger.warning("alpha_vantage.income_error", ticker=ticker, error=str(exc))
            return []

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http_client.close()
