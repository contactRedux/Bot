"""
Central configuration module using pydantic-settings.

All settings are loaded from environment variables (or a .env file at the repo
root).  The TRADING_MODE variable controls which broker adapter is instantiated:

    dev   — no real data feeds; uses cached/mock data for fast iteration
    paper — live data feeds + paper (simulated) order execution
    live  — live data feeds + real order execution via Alpaca / Binance

Usage
-----
    from config.settings import settings

    if settings.trading_mode == "live":
        # gates any code that touches real money
        ...
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ── Enums ────────────────────────────────────────────────────────────────────

class TradingMode(str, Enum):
    DEV = "dev"
    PAPER = "paper"
    LIVE = "live"


# ── Settings model ────────────────────────────────────────────────────────────

class Settings(BaseSettings):
    """
    All configuration for the quant-engine, loaded from environment variables.

    Fields map 1-to-1 with entries in the root .env.example file.
    Any field with a default of None is optional at startup but may be
    required at runtime (e.g. ALPACA_API_KEY is only required in paper/live
    mode — the factory in execution/factory.py enforces this).
    """

    model_config = SettingsConfigDict(
        # Walk up from this file's location to find the repo-root .env
        env_file=str(Path(__file__).resolve().parents[3] / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Mode ─────────────────────────────────────────────────────────────────
    trading_mode: TradingMode = Field(
        default=TradingMode.DEV,
        description="Execution mode: dev | paper | live",
    )

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="sqlite:///./algo_trading.db",
        description=(
            "SQLAlchemy connection string. "
            "SQLite for dev/backtest; PostgreSQL for production. "
            "Example: postgresql+asyncpg://user:pass@localhost/algodb"
        ),
    )

    # ── Alpaca (equities + paper trading) ────────────────────────────────────
    alpaca_api_key: str | None = Field(
        default=None,
        description="Alpaca API key — required for paper and live modes",
    )
    alpaca_secret_key: str | None = Field(
        default=None,
        description="Alpaca secret key — required for paper and live modes",
    )
    alpaca_base_url: str = Field(
        default="https://paper-api.alpaca.markets",
        description=(
            "Alpaca REST base URL. "
            "paper-api.alpaca.markets for paper mode; "
            "api.alpaca.markets for live."
        ),
    )

    # ── Binance (crypto) ──────────────────────────────────────────────────────
    binance_api_key: str | None = Field(
        default=None,
        description="Binance API key — required for live crypto execution",
    )
    binance_secret_key: str | None = Field(
        default=None,
        description="Binance secret key — required for live crypto execution",
    )
    binance_testnet: bool = Field(
        default=True,
        description="Use Binance testnet when True (recommended for dev/paper)",
    )

    # ── News feeds ───────────────────────────────────────────────────────────
    newsapi_key: str | None = Field(
        default=None,
        description="NewsAPI.org API key — free tier: 100 requests/day",
    )

    # ── Alpha Vantage (fundamentals) ─────────────────────────────────────────
    alpha_vantage_key: str | None = Field(
        default=None,
        description="Alpha Vantage API key — free tier: 5 requests/min, 500/day",
    )

    # ── Polygon.io (tick data / Phase 2 options) ──────────────────────────────
    polygon_key: str | None = Field(
        default=None,
        description=(
            "Polygon.io API key — optional; used for tick-level data and "
            "Phase 2 options chain. Free tier available."
        ),
    )

    # ── Feature pipeline ─────────────────────────────────────────────────────
    strategy_config_path: str = Field(
        default=str(Path(__file__).parent / "strategy_config.yaml"),
        description="Path to the YAML file containing per-strategy parameters",
    )

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = Field(
        default="INFO",
        description="Logging level: DEBUG | INFO | WARNING | ERROR",
    )
    log_json: bool = Field(
        default=False,
        description="Emit logs as JSON (useful for log aggregation in production)",
    )

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("trading_mode", mode="before")
    @classmethod
    def coerce_mode(cls, v: str) -> str:
        """Accept upper or lower case mode strings."""
        return v.lower() if isinstance(v, str) else v


# ── Singleton instance ────────────────────────────────────────────────────────
# Import this object everywhere in the codebase:
#   from config.settings import settings
settings = Settings()
