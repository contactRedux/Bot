"""
data/feeds/sec_edgar_feed.py — Earnings filings and EPS surprise via SEC EDGAR.

The SEC's EDGAR (Electronic Data Gathering, Analysis, and Retrieval) system
is a free, publicly accessible database of all financial filings submitted to
the US Securities and Exchange Commission.  It contains:

* 10-K (annual reports)
* 10-Q (quarterly reports)
* 8-K (material events — earnings releases, M&A announcements, etc.)
* DEF 14A (proxy statements)
* S-1 (IPO registrations)

Why SEC EDGAR for trading?
--------------------------
Earnings surprises (actual EPS vs. analyst consensus) are one of the strongest
documented anomalies in academic finance (Ball & Brown, 1968; Post-Earnings
Announcement Drift).  A company that beats consensus by > 2σ typically
continues drifting upward for 60–90 days.

EDGAR gives us two things:
1. **Actual reported EPS** from 10-Q / 10-K filings.
2. **Earnings release dates** from 8-K filings, which tell us exactly when
   the information became public (critical for avoiding look-ahead bias).

EDGAR API (modern, JSON)
------------------------
Since 2022, the SEC provides a structured JSON API at ``data.sec.gov``:

* Company facts (all XBRL-tagged data for a company):
  ``https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json``

* Company concept (specific XBRL concept over time):
  ``https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/{taxonomy}/{tag}.json``

* Company submissions (filing history):
  ``https://data.sec.gov/submissions/CIK{cik}.json``

The XBRL concept ``us-gaap/EarningsPerShareDiluted`` gives us diluted EPS
reported in every 10-Q and 10-K filing.

CIK lookup
----------
EDGAR uses CIK (Central Index Key) numbers, not ticker symbols.  We resolve
tickers to CIKs via:
``https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=&CIK={ticker}&type=&dateb=&owner=include&count=10&search_text=&action=getcompany``
Or the simpler bulk mapping file:
``https://www.sec.gov/files/company_tickers.json``

Earnings surprise calculation
------------------------------
SEC EDGAR does not provide analyst consensus estimates — it only has actual
reported figures.  For the consensus estimate we either:
1. Use Alpha Vantage's ``EARNINGS`` endpoint (preferred — see alpha_vantage_feed.py)
2. Estimate consensus from prior-period EPS (simple naive estimate: last period's EPS)

When Alpha Vantage is not available, the EDGAR feed uses option 2 (naive
estimate from prior period).  The ``FundamentalSnapshot.eps_surprise`` field
will then reflect the trend change rather than analyst beat/miss, which is a
weaker signal.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import structlog

from data.feeds.base import DataFeed
from data.schemas import FundamentalSnapshot

logger = structlog.get_logger(__name__)

# SEC EDGAR API base URLs (no authentication required, but SEC asks for User-Agent)
_EDGAR_DATA_URL = "https://data.sec.gov"
_EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# SEC requires a User-Agent identifying who is making the request
_USER_AGENT = "quant-engine-algo-trading research@example.com"


class SecEdgarFeed(DataFeed):
    """
    Fundamental data feed backed by the SEC EDGAR XBRL API.

    Fetches actual reported EPS from 10-Q/10-K filings and computes
    earnings surprise using either a provided consensus estimate or a
    naive prior-period estimate.

    Parameters
    ----------
    config : dict, optional
        Accepts:
        - ``"user_agent"`` : HTTP User-Agent string (required by SEC policy;
          defaults to a placeholder — **replace with your actual contact info**)
        - ``"request_delay"`` : float seconds between requests (default 0.5;
          SEC asks for no more than 10 requests/second)

    Notes
    -----
    The SEC's Fair Access Policy requires that automated scripts identify
    themselves via the ``User-Agent`` header with a company name and email.
    Violating this may result in IP bans.  Always set a real User-Agent.
    """

    SOURCE = "sec_edgar"

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        user_agent = self.config.get("user_agent", _USER_AGENT)
        self._request_delay: float = self.config.get("request_delay", 0.5)
        self._http_client = httpx.Client(
            timeout=30.0,
            headers={
                "User-Agent": user_agent,
                "Accept": "application/json",
            },
        )
        self._ticker_to_cik: dict[str, str] = {}

    def _load_ticker_map(self) -> None:
        """
        Download the SEC's bulk ticker→CIK mapping file.

        The file is a JSON object like:
        ``{"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}``

        We invert it to a ticker→CIK dict for fast lookups.
        """
        if self._ticker_to_cik:
            return  # already loaded

        logger.info("sec_edgar.load_ticker_map")
        try:
            resp = self._http_client.get(_EDGAR_TICKERS_URL)
            resp.raise_for_status()
            data = resp.json()

            for entry in data.values():
                ticker = entry.get("ticker", "").upper()
                cik = str(entry.get("cik_str", "")).zfill(10)
                if ticker:
                    self._ticker_to_cik[ticker] = cik

            logger.info(
                "sec_edgar.ticker_map_loaded",
                total_tickers=len(self._ticker_to_cik),
            )
        except Exception as exc:
            logger.error("sec_edgar.ticker_map_error", error=str(exc))

    def _get_cik(self, ticker: str) -> str | None:
        """Resolve a ticker symbol to an SEC CIK number."""
        self._load_ticker_map()
        return self._ticker_to_cik.get(ticker.upper())

    def fetch_fundamentals(
        self,
        ticker: str,
        period: str = "quarterly",
    ) -> list[FundamentalSnapshot]:
        """
        Fetch EPS and earnings dates from SEC EDGAR XBRL data.

        Retrieves the ``EarningsPerShareDiluted`` concept from the company's
        XBRL filings and assembles ``FundamentalSnapshot`` records.

        Each filing is identified by:
        * ``period_end_date`` — the last day of the fiscal period (from ``end`` in XBRL)
        * ``report_date``     — when the 10-Q/10-K was filed (from ``filed`` in XBRL)
          This is used as ``event_timestamp`` — the data is only available after filing.

        EPS surprise is computed relative to the immediately preceding period's
        reported EPS (naive estimate).  Pair with ``AlphaVantageFeed`` for analyst
        consensus-based surprise.

        Parameters
        ----------
        ticker :
            US equity ticker symbol.
        period :
            ``"quarterly"`` (10-Q filings) or ``"annual"`` (10-K filings).

        Returns
        -------
        list[FundamentalSnapshot]
            Sorted descending by ``period_end_date``.
        """
        cik = self._get_cik(ticker)
        if not cik:
            logger.warning("sec_edgar.cik_not_found", ticker=ticker)
            return []

        logger.info("sec_edgar.fetch_fundamentals", ticker=ticker, cik=cik, period=period)

        eps_records = self._fetch_eps_concept(cik, ticker)
        if not eps_records:
            return []

        # Filter by form type (10-Q for quarterly, 10-K for annual)
        form_type = "10-Q" if period == "quarterly" else "10-K"
        filtered = [r for r in eps_records if r.get("form") == form_type]

        if not filtered:
            # Fallback: use all records if form filtering yields nothing
            filtered = eps_records

        # Sort by period end date (ascending) to compute naive consensus
        filtered.sort(key=lambda r: r.get("end", ""))
        fetch_ts = datetime.now(tz=timezone.utc)
        snapshots: list[FundamentalSnapshot] = []

        prev_eps: float | None = None  # used for naive EPS surprise estimate

        for record in filtered:
            try:
                period_end = _parse_edgar_date(record.get("end"))
                filed_date = _parse_edgar_date(record.get("filed"))
                if period_end is None:
                    continue
                if filed_date is None:
                    filed_date = period_end

                eps_value = _safe_float(record.get("val"))

                # Naive earnings surprise: deviation from prior period's EPS
                # This is a weak signal; prefer Alpha Vantage consensus estimates.
                eps_consensus = prev_eps
                prev_eps = eps_value

                snapshot = FundamentalSnapshot(
                    ticker=ticker,
                    period=period,  # type: ignore[arg-type]
                    period_end_date=period_end,
                    report_date=filed_date,
                    eps_reported=eps_value,
                    eps_consensus=eps_consensus,
                    # eps_surprise auto-computed in model_post_init
                    currency="USD",
                    source=self.SOURCE,
                    event_timestamp=filed_date,
                    fetch_timestamp=fetch_ts,
                )
                snapshots.append(snapshot)
            except Exception as exc:
                logger.warning(
                    "sec_edgar.snapshot_parse_error",
                    ticker=ticker,
                    error=str(exc),
                )

        # Return most recent first
        snapshots.sort(key=lambda s: s.period_end_date, reverse=True)
        logger.info(
            "sec_edgar.fetch_fundamentals.done",
            ticker=ticker,
            snapshots_returned=len(snapshots),
        )
        return snapshots

    def _fetch_eps_concept(self, cik: str, ticker: str) -> list[dict]:
        """
        Fetch the EarningsPerShareDiluted XBRL concept for a company.

        Returns a list of filing records, each with fields:
        ``{end, filed, form, val, accn, fy, fp, frame}``
        """
        import time

        url = (
            f"{_EDGAR_DATA_URL}/api/xbrl/companyconcept/"
            f"CIK{cik}/us-gaap/EarningsPerShareDiluted.json"
        )
        try:
            time.sleep(self._request_delay)
            resp = self._http_client.get(url)
            resp.raise_for_status()
            data = resp.json()

            # The JSON has a nested structure:
            # data["units"]["USD"] = list of filing records
            units = data.get("units", {})
            usd_records = units.get("USD", [])
            return usd_records
        except Exception as exc:
            logger.error(
                "sec_edgar.eps_concept_error",
                ticker=ticker,
                cik=cik,
                error=str(exc),
            )
            return []

    def fetch_recent_8k_filings(self, ticker: str, max_results: int = 10) -> list[dict]:
        """
        Fetch the most recent 8-K filings for a ticker.

        8-K filings are "material event" reports that companies must file
        within 4 business days of a significant event such as:
        * Earnings announcements
        * M&A announcements
        * Leadership changes
        * Material contracts

        This method returns raw submission metadata.  For the DataPipeline,
        8-K monitoring is used to trigger immediate news article creation
        when a material event is detected.

        Parameters
        ----------
        ticker :
            US equity ticker symbol.
        max_results :
            Maximum number of 8-K filings to return.

        Returns
        -------
        list[dict]
            Filing metadata records sorted newest first.
        """
        import time

        cik = self._get_cik(ticker)
        if not cik:
            return []

        url = f"{_EDGAR_DATA_URL}/submissions/CIK{cik}.json"
        try:
            time.sleep(self._request_delay)
            resp = self._http_client.get(url)
            resp.raise_for_status()
            data = resp.json()

            filings = data.get("filings", {}).get("recent", {})
            forms = filings.get("form", [])
            dates = filings.get("filingDate", [])
            descriptions = filings.get("primaryDocDescription", [])

            results = []
            for i, form in enumerate(forms):
                if form == "8-K" and len(results) < max_results:
                    results.append({
                        "form": form,
                        "filing_date": dates[i] if i < len(dates) else None,
                        "description": descriptions[i] if i < len(descriptions) else None,
                        "ticker": ticker,
                        "cik": cik,
                    })
            return results
        except Exception as exc:
            logger.error("sec_edgar.8k_error", ticker=ticker, error=str(exc))
            return []

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http_client.close()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_edgar_date(date_str: str | None) -> datetime | None:
    """Parse EDGAR date strings ``'YYYY-MM-DD'`` to UTC datetime."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _safe_float(value) -> float | None:
    """Convert to float, returning None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
