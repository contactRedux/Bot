"""
Structured logging setup using structlog.

Import and call `get_logger(__name__)` in any module:

    from config.logging import get_logger
    log = get_logger(__name__)
    log.info("bar_received", ticker="AAPL", close=182.45)

In dev mode logs are pretty-printed with colours.
In production (log_json=True) logs are emitted as JSON for log aggregation.
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(log_level: str = "INFO", log_json: bool = False) -> None:
    """
    Initialise structlog.  Call this once at application startup
    (e.g. in api/main.py or backtesting/runner.py).
    """

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if log_json:
        # Machine-readable JSON — good for ELK / Datadog
        renderer = structlog.processors.JSONRenderer()
    else:
        # Human-readable coloured output for terminal development
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger for `name`."""
    return structlog.get_logger(name)
