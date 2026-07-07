"""
features/fundamental.py — Fundamental data feature engineering.

Transforms raw ``FundamentalSnapshot`` records into normalized ML features
that can be used as inputs to the Macro Factor strategy and gradient-boosting
models.

Why normalize fundamental data?
--------------------------------
Raw fundamental values (P/E = 25, revenue = $119B) are not useful as ML inputs
because they are not comparable across companies or over time.  A P/E of 25 is
cheap for a growth company and expensive for a mature utility.  We normalize by:

1. **Sector z-score** — compare each company's ratio to the sector median:
   z = (ratio_company − mean_sector) / std_sector

2. **Historical z-score** — compare a company's current ratio to its own
   trailing history:
   z = (ratio_t − rolling_mean(ratio)) / rolling_std(ratio)

3. **Growth rates** — period-over-period percentage change (first difference
   of log values).

These normalized features are scale-invariant and comparable across all tickers.

Feature list
------------
  ``pe_zscore_hist``        — P/E z-score vs. company's own trailing history
  ``pb_zscore_hist``        — P/B z-score vs. company's own trailing history
  ``eps_growth``            — Quarter-over-quarter EPS growth rate
  ``revenue_growth``        — Quarter-over-quarter revenue growth rate
  ``earnings_surprise``     — (reported − consensus) / |consensus|
  ``eps_surprise_abs``      — |earnings_surprise| (magnitude, direction-agnostic)
  ``gross_margin``          — gross_profit / revenue
  ``operating_margin``      — operating_income / revenue
  ``net_margin``            — net_income / revenue
  ``roe``                   — return on equity (direct from source)

Look-ahead bias
---------------
The ``report_date`` field of each snapshot is used as the event_timestamp.
Features are only available *after* ``report_date``.  The FeaturePipeline
aligns fundamental features to the price bar index by forward-filling, which
means the feature value for bar T reflects the most recently filed report
*before* T.  The DataStore enforces this via the event_timestamp field.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from data.schemas import FundamentalSnapshot


def snapshots_to_dataframe(
    snapshots: list[FundamentalSnapshot],
    date_col: str = "report_date",
) -> pd.DataFrame:
    """
    Convert a list of ``FundamentalSnapshot`` records to a pandas DataFrame.

    The DataFrame is indexed by ``report_date`` (UTC), sorted ascending.
    Missing numeric fields are represented as NaN.

    Parameters
    ----------
    snapshots : list[FundamentalSnapshot]
        Fundamental snapshots for a single ticker, any period type.
    date_col : str
        Which date field to use as the index: ``"report_date"`` (default,
        when the data became *available*) or ``"period_end_date"`` (fiscal
        period end).  Always use ``"report_date"`` for backtesting to avoid
        look-ahead bias.

    Returns
    -------
    pd.DataFrame
        Rows sorted ascending by the chosen date index.
    """
    if not snapshots:
        return pd.DataFrame()

    rows = []
    for s in snapshots:
        ts = getattr(s, date_col)
        rows.append({
            "ticker": s.ticker,
            "period": s.period,
            "event_timestamp": ts,
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
        })

    df = pd.DataFrame(rows).set_index("event_timestamp").sort_index()
    return df


def add_fundamental_features(
    fund_df: pd.DataFrame,
    rolling_zscore_window: int = 8,
) -> pd.DataFrame:
    """
    Compute normalized fundamental ML features from a raw fundamentals DataFrame.

    The input is produced by ``snapshots_to_dataframe``.  All growth rates and
    z-scores are computed using only past data (rolling backward-looking windows).

    Parameters
    ----------
    fund_df : pd.DataFrame
        Raw fundamental data indexed by report_date, sorted ascending.
    rolling_zscore_window : int
        Number of periods for historical z-score normalization.  Default 8
        (2 years of quarterly data).

    Returns
    -------
    pd.DataFrame
        Feature columns appended to the input, same index.  Raw ratio
        columns are preserved; computed feature columns are added.
    """
    if fund_df.empty:
        return fund_df

    df = fund_df.copy()
    w = rolling_zscore_window

    # ── Margin ratios ─────────────────────────────────────────────────────────
    rev = df["revenue"].replace(0, np.nan)
    df["gross_margin"] = df["gross_profit"] / rev
    df["operating_margin"] = df["operating_income"] / rev
    df["net_margin"] = df["net_income"] / rev

    # ── Period-over-period growth rates ───────────────────────────────────────
    # pct_change() computes (current − prior) / |prior|, which is exactly the
    # growth rate.  min_periods=2 ensures the first row stays NaN.
    df["revenue_growth"] = df["revenue"].pct_change(fill_method=None)
    df["eps_growth"] = df["eps_reported"].pct_change(fill_method=None)
    df["net_income_growth"] = df["net_income"].pct_change(fill_method=None)

    # ── Earnings surprise ─────────────────────────────────────────────────────
    # eps_surprise may already be set (computed in FundamentalSnapshot.model_post_init)
    # but we ensure it is set here too in case the DataFrame came from raw storage.
    mask = (
        df["eps_surprise"].isna()
        & df["eps_reported"].notna()
        & df["eps_consensus"].notna()
        & (df["eps_consensus"] != 0)
    )
    df.loc[mask, "eps_surprise"] = (
        (df.loc[mask, "eps_reported"] - df.loc[mask, "eps_consensus"])
        / df.loc[mask, "eps_consensus"].abs()
    )
    df["eps_surprise_abs"] = df["eps_surprise"].abs()

    # ── Historical z-scores of valuation ratios ───────────────────────────────
    # z = (value − rolling_mean) / rolling_std over trailing w periods
    # Using the company's own history makes the z-score self-normalizing.
    for col, new_col in [
        ("pe_ratio", "pe_zscore_hist"),
        ("pb_ratio", "pb_zscore_hist"),
        ("ev_ebitda", "ev_ebitda_zscore_hist"),
        ("debt_to_equity", "de_zscore_hist"),
        ("return_on_equity", "roe_zscore_hist"),
    ]:
        if col in df.columns:
            roll_mean = df[col].rolling(w, min_periods=2).mean()
            roll_std = df[col].rolling(w, min_periods=2).std(ddof=1).replace(0, np.nan)
            df[new_col] = (df[col] - roll_mean) / roll_std

    return df


def align_fundamentals_to_price_index(
    fund_features: pd.DataFrame,
    price_index: pd.DatetimeIndex,
    max_fill_periods: int = 90,
) -> pd.DataFrame:
    """
    Forward-fill fundamental features onto a higher-frequency price bar index.

    Fundamental data is reported quarterly; price bars are daily.  To use
    fundamental features as inputs to daily models, we forward-fill the most
    recent available snapshot onto each daily bar.

    ``max_fill_periods`` caps the forward-fill to avoid stale data propagating
    too far.  After 90 days, the feature value goes NaN to signal that the
    data has not been refreshed.

    Parameters
    ----------
    fund_features : pd.DataFrame
        Quarterly fundamental features indexed by report_date.
    price_index : pd.DatetimeIndex
        The daily (or other frequency) index to align onto.
    max_fill_periods : int
        Maximum number of price bars to forward-fill across.

    Returns
    -------
    pd.DataFrame
        Fundamental features on the price bar index, forward-filled.
    """
    if fund_features.empty:
        return pd.DataFrame(index=price_index)

    # Combine indexes, forward-fill, then select only the price index rows
    combined_idx = fund_features.index.union(price_index).sort_values()
    reindexed = fund_features.reindex(combined_idx)
    filled = reindexed.ffill(limit=max_fill_periods)
    return filled.reindex(price_index)


def compute_sector_zscores(
    fundamentals_by_ticker: dict[str, pd.DataFrame],
    ratio_cols: list[str] | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Compute cross-sectional (sector) z-scores for valuation ratios.

    For each reporting period, z-score each company's ratio relative to all
    companies in ``fundamentals_by_ticker`` at the same date.  This answers
    the question "is AAPL cheap or expensive *relative to its peers right now?*"

    Parameters
    ----------
    fundamentals_by_ticker : dict[str, pd.DataFrame]
        Mapping from ticker to its fundamental features DataFrame
        (output of ``add_fundamental_features``).
    ratio_cols : list of str, optional
        Ratios to z-score.  Defaults to ``["pe_ratio", "pb_ratio", "ev_ebitda"]``.

    Returns
    -------
    dict[str, pd.DataFrame]
        Same keys as input; each DataFrame has new ``{col}_zscore_sector``
        columns added.
    """
    if ratio_cols is None:
        ratio_cols = ["pe_ratio", "pb_ratio", "ev_ebitda"]

    # Build a wide DataFrame: index = dates, columns = (ticker, ratio)
    result = {t: df.copy() for t, df in fundamentals_by_ticker.items()}

    for col in ratio_cols:
        # Wide form: rows=dates, columns=tickers
        wide = pd.DataFrame(
            {t: df[col] for t, df in fundamentals_by_ticker.items() if col in df.columns}
        )
        if wide.empty:
            continue
        cross_mean = wide.mean(axis=1)
        cross_std = wide.std(axis=1, ddof=1).replace(0, np.nan)

        for ticker in result:
            if col in result[ticker].columns:
                zscore_col = f"{col}_zscore_sector"
                result[ticker][zscore_col] = (
                    (result[ticker][col] - cross_mean) / cross_std
                )

    return result
