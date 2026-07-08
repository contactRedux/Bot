"""
data/store.py — DataStore: SQLAlchemy-backed persistence layer for all market data.

The DataStore is the single storage interface for all data in the quant-engine.
All feeds write through it, and all downstream consumers (feature pipeline,
strategies, backtesting engine) read through it.  This centralizes:

1. **Schema management** — SQLAlchemy ORM models define the DB schema in code.
2. **Data access** — typed read/write methods with filtering.
3. **Portability** — SQLite for dev/backtesting; PostgreSQL for production.
   Switching is a one-line change in .env (DATABASE_URL).

Database design decisions
--------------------------
* We use SQLAlchemy's **Core** (not ORM) for writes because bulk inserts are
  much faster with ``insert().values(...)`` than ORM session.add().
* We use ORM models for schema definition and reads because they provide a
  clean object interface.
* All timestamps are stored as UTC datetimes without timezone info
  (SQLite does not support timezone-aware datetimes; we convert on read).
* Duplicate prevention: bars use a unique index on (ticker, interval, event_timestamp);
  news uses a unique index on article_id.  Both use ``INSERT OR IGNORE``
  semantics (``insert_or_ignore`` via ``on_conflict_do_nothing``).

Table layout
------------
  ohlcv_bars       — OHLCVBar records (all sources, all intervals)
  news_articles    — NewsArticle records (all sources)
  fundamentals     — FundamentalSnapshot records

Note: Trade and OrderBook data is NOT persisted — it is used transiently in
memory by the real-time pipeline and discarded.  The volume of tick data and
order book snapshots (potentially millions of rows/day) would overwhelm SQLite.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from data.schemas import FundamentalSnapshot, NewsArticle, OHLCVBar

logger = structlog.get_logger(__name__)


# ── ORM base ────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ── ORM table models ─────────────────────────────────────────────────────────

class OHLCVBarRow(Base):
    """
    SQLAlchemy ORM model for the ``ohlcv_bars`` table.

    Each row represents one completed OHLCV bar for a ticker at a specific
    interval and event timestamp.

    The unique index on ``(ticker, interval, event_timestamp)`` ensures that
    re-running a historical fetch does not create duplicate rows.
    """
    __tablename__ = "ohlcv_bars"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    interval = Column(String(5), nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False, default=0.0)
    event_timestamp = Column(DateTime, nullable=False, index=True)
    fetch_timestamp = Column(DateTime, nullable=False)
    source = Column(String(30), nullable=False)
    adjusted = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("ticker", "interval", "event_timestamp", name="uq_bar"),
    )


class NewsArticleRow(Base):
    """
    SQLAlchemy ORM model for the ``news_articles`` table.

    The unique index on ``article_id`` ensures idempotent inserts — the same
    article fetched twice will not create a duplicate row.

    ``tickers`` is stored as a comma-separated string (simple, no join table
    needed for the single-user use case).
    ``sentiment_score`` starts as NULL and is updated after FinBERT scoring
    in the feature pipeline.
    """
    __tablename__ = "news_articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    article_id = Column(String(64), nullable=False, unique=True, index=True)
    title = Column(Text, nullable=False)
    body = Column(Text, nullable=True)
    url = Column(Text, nullable=True)
    source = Column(String(30), nullable=False)
    author = Column(String(200), nullable=True)
    tickers = Column(String(500), nullable=True)  # comma-separated
    event_timestamp = Column(DateTime, nullable=False, index=True)
    fetch_timestamp = Column(DateTime, nullable=False)
    sentiment_score = Column(Float, nullable=True)


class FundamentalRow(Base):
    """
    SQLAlchemy ORM model for the ``fundamentals`` table.

    Unique index on ``(ticker, period, period_end_date)`` prevents duplicate
    snapshots for the same reporting period across multiple fetches.
    """
    __tablename__ = "fundamentals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    period = Column(String(10), nullable=False)
    period_end_date = Column(DateTime, nullable=False)
    report_date = Column(DateTime, nullable=False)
    revenue = Column(Float, nullable=True)
    gross_profit = Column(Float, nullable=True)
    operating_income = Column(Float, nullable=True)
    net_income = Column(Float, nullable=True)
    eps_reported = Column(Float, nullable=True)
    eps_consensus = Column(Float, nullable=True)
    eps_surprise = Column(Float, nullable=True)
    pe_ratio = Column(Float, nullable=True)
    pb_ratio = Column(Float, nullable=True)
    ev_ebitda = Column(Float, nullable=True)
    debt_to_equity = Column(Float, nullable=True)
    return_on_equity = Column(Float, nullable=True)
    currency = Column(String(5), nullable=False, default="USD")
    source = Column(String(30), nullable=False)
    event_timestamp = Column(DateTime, nullable=False)
    fetch_timestamp = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "ticker", "period", "period_end_date", name="uq_fundamental"
        ),
    )


# ── DataStore ────────────────────────────────────────────────────────────────

class DataStore:
    """
    Unified storage interface for all market data.

    Wraps a SQLAlchemy engine to provide typed read/write methods for bars,
    news, and fundamentals.  Handles schema creation, connection management,
    and idempotent (duplicate-safe) inserts.

    Parameters
    ----------
    database_url : str
        SQLAlchemy connection string.
        Examples:
        - ``"sqlite:///./algo_trading.db"``         — local SQLite file
        - ``"sqlite:///:memory:"``                  — in-memory SQLite (tests)
        - ``"postgresql+psycopg2://user:pw@host/db"``  — PostgreSQL

    echo : bool
        If True, SQLAlchemy echoes all SQL to stdout.  Use only for debugging.

    Example
    -------
    ::

        from data.store import DataStore
        from data.feeds.yfinance_feed import YFinanceFeed
        from datetime import datetime, timezone

        store = DataStore("sqlite:///./algo_trading.db")
        feed = YFinanceFeed()
        bars = feed.fetch_bars("AAPL", "1d",
                               datetime(2023,1,1, tzinfo=timezone.utc),
                               datetime(2024,1,1, tzinfo=timezone.utc))
        n = store.write_bars(bars)
        print(f"Stored {n} bars")

        aapl_bars = store.read_bars("AAPL", "1d",
                                    datetime(2023,6,1, tzinfo=timezone.utc),
                                    datetime(2023,12,31, tzinfo=timezone.utc))
    """

    def __init__(self, database_url: str, echo: bool = False) -> None:
        self._engine = create_engine(database_url, echo=echo)

        # Enable WAL mode for SQLite — dramatically improves concurrent read
        # performance when the data pipeline is writing while the API reads.
        if database_url.startswith("sqlite"):
            @event.listens_for(self._engine, "connect")
            def _set_sqlite_pragma(dbapi_conn, connection_record):
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA foreign_keys=ON;")
                cursor.close()

        # Create all tables if they don't exist
        Base.metadata.create_all(self._engine)
        self._Session = sessionmaker(bind=self._engine, expire_on_commit=False)
        logger.info("datastore.initialized", database_url=database_url)

    # ── OHLCV Bars ───────────────────────────────────────────────────────────

    def write_bars(self, bars: list[OHLCVBar]) -> int:
        """
        Persist a list of OHLCVBar records to the database.

        Uses ``INSERT OR IGNORE`` semantics — existing rows (same ticker,
        interval, event_timestamp) are silently skipped, making this safe to
        call repeatedly with overlapping date ranges.

        Parameters
        ----------
        bars :
            List of ``OHLCVBar`` Pydantic models from any data feed.

        Returns
        -------
        int
            Number of *new* rows inserted (existing duplicates are excluded).
        """
        if not bars:
            return 0

        rows = [
            {
                "ticker": b.ticker,
                "interval": b.interval,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                # Store as tz-naive UTC (SQLite limitation)
                "event_timestamp": b.event_timestamp.replace(tzinfo=None),
                "fetch_timestamp": b.fetch_timestamp.replace(tzinfo=None),
                "source": b.source,
                "adjusted": b.adjusted,
            }
            for b in bars
        ]

        stmt = sqlite_insert(OHLCVBarRow).values(rows)
        do_nothing = stmt.on_conflict_do_nothing(index_elements=["ticker", "interval", "event_timestamp"])

        with self._Session() as session:
            result = session.execute(do_nothing)
            session.commit()
            inserted = result.rowcount

        logger.debug("datastore.write_bars", attempted=len(bars), inserted=inserted)
        return inserted

    def read_bars(
        self,
        ticker: str,
        interval: str,
        start: datetime,
        end: datetime,
        source: str | None = None,
    ) -> list[OHLCVBar]:
        """
        Read OHLCV bars for a single ticker from the database.

        Parameters
        ----------
        ticker :
            Ticker symbol.
        interval :
            Bar duration string.
        start, end :
            UTC datetime range (inclusive on both ends).
        source :
            If provided, filter by data source (e.g. ``"yfinance"``).

        Returns
        -------
        list[OHLCVBar]
            Bars sorted ascending by ``event_timestamp``.
        """
        with self._Session() as session:
            query = (
                session.query(OHLCVBarRow)
                .filter(
                    OHLCVBarRow.ticker == ticker,
                    OHLCVBarRow.interval == interval,
                    OHLCVBarRow.event_timestamp >= start.replace(tzinfo=None),
                    OHLCVBarRow.event_timestamp <= end.replace(tzinfo=None),
                )
                .order_by(OHLCVBarRow.event_timestamp)
            )
            if source:
                query = query.filter(OHLCVBarRow.source == source)
            rows = query.all()

        return [_row_to_bar(r) for r in rows]

    def get_latest_bar_timestamp(
        self, ticker: str, interval: str
    ) -> datetime | None:
        """
        Return the most recent ``event_timestamp`` for a ticker/interval.

        Used by the DataPipeline to determine where to start fetching
        new bars (avoiding redundant re-fetching of historical data).

        Returns
        -------
        datetime | None
            UTC datetime of the latest stored bar, or None if no data exists.
        """
        with self._Session() as session:
            row = (
                session.query(OHLCVBarRow.event_timestamp)
                .filter(
                    OHLCVBarRow.ticker == ticker,
                    OHLCVBarRow.interval == interval,
                )
                .order_by(OHLCVBarRow.event_timestamp.desc())
                .first()
            )
        if row is None:
            return None
        ts = row[0]
        return ts.replace(tzinfo=None) if ts else None

    # ── News ─────────────────────────────────────────────────────────────────

    def write_news(self, articles: list[NewsArticle]) -> int:
        """
        Persist news articles to the database.

        Idempotent — duplicate ``article_id`` values are silently skipped.

        Parameters
        ----------
        articles :
            List of ``NewsArticle`` Pydantic models.

        Returns
        -------
        int
            Number of new rows inserted.
        """
        if not articles:
            return 0

        rows = [
            {
                "article_id": a.article_id,
                "title": a.title,
                "body": a.body,
                "url": str(a.url) if a.url else None,
                "source": a.source,
                "author": a.author,
                "tickers": ",".join(a.tickers) if a.tickers else None,
                "event_timestamp": a.event_timestamp.replace(tzinfo=None),
                "fetch_timestamp": a.fetch_timestamp.replace(tzinfo=None),
                "sentiment_score": a.sentiment_score,
            }
            for a in articles
        ]

        stmt = sqlite_insert(NewsArticleRow).values(rows)
        do_nothing = stmt.on_conflict_do_nothing(index_elements=["article_id"])

        with self._Session() as session:
            result = session.execute(do_nothing)
            session.commit()
            inserted = result.rowcount

        logger.debug("datastore.write_news", attempted=len(articles), inserted=inserted)
        return inserted

    def read_news(
        self,
        tickers: list[str] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        source: str | None = None,
        max_results: int = 500,
        scored_only: bool = False,
    ) -> list[NewsArticle]:
        """
        Read news articles from the database.

        Parameters
        ----------
        tickers :
            Filter to articles associated with these tickers (substring match
            on the stored comma-separated tickers column).
        start, end :
            UTC datetime range filter on ``event_timestamp``.
        source :
            Filter by news source (``"newsapi"``, ``"gdelt"``, etc.).
        max_results :
            Maximum rows to return.
        scored_only :
            If True, only return articles where ``sentiment_score`` is not NULL.

        Returns
        -------
        list[NewsArticle]
            Sorted descending by ``event_timestamp`` (newest first).
        """
        with self._Session() as session:
            query = session.query(NewsArticleRow)

            if start:
                query = query.filter(
                    NewsArticleRow.event_timestamp >= start.replace(tzinfo=None)
                )
            if end:
                query = query.filter(
                    NewsArticleRow.event_timestamp <= end.replace(tzinfo=None)
                )
            if source:
                query = query.filter(NewsArticleRow.source == source)
            if scored_only:
                query = query.filter(NewsArticleRow.sentiment_score.isnot(None))

            if tickers:
                # Filter for articles that mention any of the requested tickers.
                # The tickers column is comma-separated; we use LIKE for simplicity.
                from sqlalchemy import or_
                ticker_filters = [
                    NewsArticleRow.tickers.like(f"%{t}%") for t in tickers
                ]
                query = query.filter(or_(*ticker_filters))

            rows = (
                query.order_by(NewsArticleRow.event_timestamp.desc())
                .limit(max_results)
                .all()
            )

        return [_row_to_news(r) for r in rows]

    def update_sentiment_score(self, article_id: str, score: float) -> None:
        """
        Update the sentiment score for a stored article.

        Called by the sentiment feature module after FinBERT scoring.

        Parameters
        ----------
        article_id :
            The unique identifier of the article to update.
        score :
            FinBERT score in ``[-1, +1]``.
        """
        with self._Session() as session:
            session.query(NewsArticleRow).filter(
                NewsArticleRow.article_id == article_id
            ).update({"sentiment_score": score})
            session.commit()

    # ── Fundamentals ─────────────────────────────────────────────────────────

    def write_fundamentals(self, snapshots: list[FundamentalSnapshot]) -> int:
        """
        Persist fundamental snapshots to the database.

        Idempotent — duplicate ``(ticker, period, period_end_date)`` rows are
        silently skipped.

        Parameters
        ----------
        snapshots :
            List of ``FundamentalSnapshot`` Pydantic models.

        Returns
        -------
        int
            Number of new rows inserted.
        """
        if not snapshots:
            return 0

        rows = [
            {
                "ticker": s.ticker,
                "period": s.period,
                "period_end_date": s.period_end_date.replace(tzinfo=None),
                "report_date": s.report_date.replace(tzinfo=None),
                "revenue": s.revenue,
                "gross_profit": s.gross_profit,
                "operating_income": s.operating_income,
                "net_income": s.net_income,
                "eps_reported": s.eps_reported,
                "eps_consensus": s.eps_consensus,
                "eps_surprise": s.eps_surprise,
                "pe_ratio": s.pe_ratio,
                "pb_ratio": s.pb_ratio,
                "ev_ebitda": s.ev_ebitda,
                "debt_to_equity": s.debt_to_equity,
                "return_on_equity": s.return_on_equity,
                "currency": s.currency,
                "source": s.source,
                "event_timestamp": s.event_timestamp.replace(tzinfo=None),
                "fetch_timestamp": s.fetch_timestamp.replace(tzinfo=None),
            }
            for s in snapshots
        ]

        stmt = sqlite_insert(FundamentalRow).values(rows)
        do_nothing = stmt.on_conflict_do_nothing(
            index_elements=["ticker", "period", "period_end_date"]
        )

        with self._Session() as session:
            result = session.execute(do_nothing)
            session.commit()
            inserted = result.rowcount

        logger.debug(
            "datastore.write_fundamentals",
            attempted=len(snapshots),
            inserted=inserted,
        )
        return inserted

    def read_fundamentals(
        self,
        ticker: str,
        period: str = "quarterly",
        start: datetime | None = None,
        end: datetime | None = None,
        max_results: int = 20,
    ) -> list[FundamentalSnapshot]:
        """
        Read fundamental snapshots for a ticker.

        Parameters
        ----------
        ticker :
            Equity ticker symbol.
        period :
            ``"quarterly"`` or ``"annual"``.
        start, end :
            Optional UTC datetime filter on ``period_end_date``.
        max_results :
            Maximum rows to return.

        Returns
        -------
        list[FundamentalSnapshot]
            Sorted descending by ``period_end_date`` (most recent first).
        """
        with self._Session() as session:
            query = (
                session.query(FundamentalRow)
                .filter(
                    FundamentalRow.ticker == ticker,
                    FundamentalRow.period == period,
                )
            )

            if start:
                query = query.filter(
                    FundamentalRow.period_end_date >= start.replace(tzinfo=None)
                )
            if end:
                query = query.filter(
                    FundamentalRow.period_end_date <= end.replace(tzinfo=None)
                )

            rows = (
                query.order_by(FundamentalRow.period_end_date.desc())
                .limit(max_results)
                .all()
            )

        return [_row_to_fundamental(r) for r in rows]

    # ── Diagnostics ──────────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, int]:
        """Return row counts for all tables (useful for health checks)."""
        with self._Session() as session:
            bars = session.query(OHLCVBarRow).count()
            news = session.query(NewsArticleRow).count()
            fundamentals = session.query(FundamentalRow).count()
        return {"bars": bars, "news": news, "fundamentals": fundamentals}


# ── Conversion helpers ───────────────────────────────────────────────────────

def _row_to_bar(row: OHLCVBarRow) -> OHLCVBar:
    """Convert an ORM row to a Pydantic OHLCVBar model (restores UTC timezone)."""
    from datetime import timezone as tz
    return OHLCVBar(
        ticker=row.ticker,
        interval=row.interval,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
        event_timestamp=row.event_timestamp.replace(tzinfo=tz.utc),
        fetch_timestamp=row.fetch_timestamp.replace(tzinfo=tz.utc),
        source=row.source,
        adjusted=row.adjusted,
    )


def _row_to_news(row: NewsArticleRow) -> NewsArticle:
    """Convert an ORM row to a Pydantic NewsArticle model."""
    from datetime import timezone as tz
    tickers = row.tickers.split(",") if row.tickers else []
    return NewsArticle(
        article_id=row.article_id,
        title=row.title,
        body=row.body,
        url=row.url,
        source=row.source,
        author=row.author,
        tickers=tickers,
        event_timestamp=row.event_timestamp.replace(tzinfo=tz.utc),
        fetch_timestamp=row.fetch_timestamp.replace(tzinfo=tz.utc),
        sentiment_score=row.sentiment_score,
    )


def _row_to_fundamental(row: FundamentalRow) -> FundamentalSnapshot:
    """Convert an ORM row to a Pydantic FundamentalSnapshot model."""
    from datetime import timezone as tz
    return FundamentalSnapshot(
        ticker=row.ticker,
        period=row.period,  # type: ignore[arg-type]
        period_end_date=row.period_end_date.replace(tzinfo=tz.utc),
        report_date=row.report_date.replace(tzinfo=tz.utc),
        revenue=row.revenue,
        gross_profit=row.gross_profit,
        operating_income=row.operating_income,
        net_income=row.net_income,
        eps_reported=row.eps_reported,
        eps_consensus=row.eps_consensus,
        eps_surprise=row.eps_surprise,
        pe_ratio=row.pe_ratio,
        pb_ratio=row.pb_ratio,
        ev_ebitda=row.ev_ebitda,
        debt_to_equity=row.debt_to_equity,
        return_on_equity=row.return_on_equity,
        currency=row.currency or "USD",
        source=row.source,
        event_timestamp=row.event_timestamp.replace(tzinfo=tz.utc),
        fetch_timestamp=row.fetch_timestamp.replace(tzinfo=tz.utc),
    )
