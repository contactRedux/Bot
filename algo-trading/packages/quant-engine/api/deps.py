"""
api/deps.py — FastAPI dependency injection providers.

FastAPI's ``Depends()`` system lets us inject shared state (broker, monitor,
orchestrator, etc.) into route functions without making them global variables.

AppState
--------
``AppState`` is a simple container attached to ``app.state`` during startup
(in ``api/main.py``'s lifespan context manager).  All route dependencies pull
from this single source of truth.

Why not globals?
----------------
Using ``app.state`` rather than module-level globals makes it easy to swap
out components in tests — just override ``app.state.monitor`` with a mock
before the test runs, and all routes that call ``get_monitor()`` will
receive the mock automatically.

Auth / RBAC
-----------
``require_operator`` is a FastAPI dependency that validates the Bearer token
and checks the required role claim.  It is a *seam*: when ``OIDC_ISSUER_URL``
is not configured (local dev), the check is skipped entirely.  In production,
set ``OIDC_ISSUER_URL``, ``OIDC_AUDIENCE``, and ``API_REQUIRED_ROLE`` in the
environment and the dependency will validate the JWT signature and role.

The implementation uses ``PyJWT`` (already in many FastAPI projects) for
decode-and-verify.  It does **not** build a full identity platform — it is
intentionally minimal: verify signature, check expiry, check audience, check
role claim.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bearer token extractor (optional — 401 only when OIDC is configured)
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# AppState — attached to app.state at startup
# ---------------------------------------------------------------------------

@dataclass
class AppState:
    """
    Container for all shared application components.

    Populated by the lifespan context manager in ``api/main.py``.
    Routes access this via the ``get_*`` dependency functions below.
    """
    # Core components (set during lifespan startup)
    broker: Any = None             # ExecutionBroker
    monitor: Any = None            # DrawdownMonitor
    risk_manager: Any = None       # RiskManager
    orchestrator: Any = None       # StrategyOrchestrator
    portfolio: Any = None          # Portfolio (live state)
    data_store: Any = None         # DataStore (SQLAlchemy-backed market data)

    # Backtest run cache: run_id → BacktestResponse dict
    backtest_results: dict[str, dict] = field(default_factory=dict)
    # In-progress run tracking: run_id → {"status", "progress_pct", "message"}
    backtest_status: dict[str, dict] = field(default_factory=dict)

    # Latest signals cache: strategy_id → list[SignalItem dict]
    latest_signals: list[dict] = field(default_factory=list)

    # Latest equity curve snapshot for VaR computation
    equity_history: list[float] = field(default_factory=list)

    # Start time for uptime calculation
    started_at: float = field(default_factory=time.time)

    # App version (injected from pyproject or env)
    version: str = "0.1.0"

    # Trading mode string (dev / paper / live)
    trading_mode: str = "dev"


# ---------------------------------------------------------------------------
# Dependency providers
# ---------------------------------------------------------------------------

def get_app_state(request: Request) -> AppState:
    """Return the AppState attached to the running FastAPI app."""
    state: AppState = request.app.state.app_state  # type: ignore[attr-defined]
    return state


def get_monitor(request: Request) -> Any:
    """
    Return the DrawdownMonitor.

    Raises 503 if the monitor was not initialised (startup failed or
    called before lifespan startup completed).
    """
    state: AppState = request.app.state.app_state
    if state.monitor is None:
        raise HTTPException(
            status_code=503,
            detail="DrawdownMonitor not initialised. Is the server still starting up?",
        )
    return state.monitor


def get_risk_manager(request: Request) -> Any:
    """Return the RiskManager, or 503 if not initialised."""
    state: AppState = request.app.state.app_state
    if state.risk_manager is None:
        raise HTTPException(
            status_code=503,
            detail="RiskManager not initialised.",
        )
    return state.risk_manager


def get_broker(request: Request) -> Any:
    """Return the ExecutionBroker, or 503 if not initialised."""
    state: AppState = request.app.state.app_state
    if state.broker is None:
        raise HTTPException(
            status_code=503,
            detail="ExecutionBroker not initialised.",
        )
    return state.broker


def get_orchestrator(request: Request) -> Any:
    """Return the StrategyOrchestrator, or 503 if not initialised."""
    state: AppState = request.app.state.app_state
    if state.orchestrator is None:
        raise HTTPException(
            status_code=503,
            detail="StrategyOrchestrator not initialised.",
        )
    return state.orchestrator


def get_portfolio(request: Request) -> Any:
    """Return the live Portfolio, or 503 if not initialised."""
    state: AppState = request.app.state.app_state
    if state.portfolio is None:
        raise HTTPException(
            status_code=503,
            detail="Portfolio not initialised.",
        )
    return state.portfolio


# ---------------------------------------------------------------------------
# Auth / RBAC dependency
# ---------------------------------------------------------------------------

def require_operator(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    """
    Dependency that enforces operator-role access on mutation endpoints.

    Behaviour
    ---------
    * When ``OIDC_ISSUER_URL`` is **not** configured (default in dev), the
      check is a no-op — the endpoint is accessible without a token.
    * When ``OIDC_ISSUER_URL`` **is** configured, a valid Bearer JWT is
      required.  The token is verified against the issuer's JWKS, the
      audience claim must match ``OIDC_AUDIENCE``, and the decoded payload
      must contain the role specified by ``API_REQUIRED_ROLE`` in either:
        - ``claims["roles"]``  (flat list, e.g. Auth0 custom claim)
        - ``claims["realm_access"]["roles"]``  (Keycloak standard)

    Raises
    ------
    HTTP 401  — missing/malformed/expired token when OIDC is configured.
    HTTP 403  — token valid but required role is absent.
    """
    try:
        from config.settings import settings
    except Exception:
        # Settings not loadable (e.g. isolated unit tests); skip auth.
        return

    if not settings.oidc_issuer_url:
        # OIDC not configured — dev/local mode, skip validation.
        return

    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Bearer token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    _validate_token_and_role(
        token=token,
        issuer=settings.oidc_issuer_url,
        audience=settings.oidc_audience,
        required_role=settings.api_required_role,
    )


def _validate_token_and_role(
    token: str,
    issuer: str,
    audience: str | None,
    required_role: str,
) -> None:
    """
    Validate a JWT and check the required role claim.

    Uses PyJWT with JWKS fetched from the issuer's well-known endpoint.
    The JWKS fetch is intentionally synchronous and short-lived; caching
    is left to the identity provider's HTTP layer.

    Raises HTTPException on any validation failure.
    """
    try:
        import jwt  # PyJWT
        from jwt import PyJWKClient
    except ImportError:
        logger.error(
            "PyJWT is not installed. "
            "Install it with: pip install 'PyJWT[cryptography]'"
        )
        raise HTTPException(
            status_code=503,
            detail="Authentication service unavailable (PyJWT not installed).",
        )

    jwks_url = issuer.rstrip("/") + "/.well-known/jwks.json"
    try:
        jwks_client = PyJWKClient(jwks_url)
        signing_key = jwks_client.get_signing_key_from_jwt(token)
    except Exception as exc:
        logger.warning("JWKS fetch/key resolution failed: %s", exc)
        raise HTTPException(
            status_code=401,
            detail="Token validation failed.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    decode_options: dict = {"require": ["exp", "iss"]}
    decode_kwargs: dict = {
        "key": signing_key.key,
        "algorithms": ["RS256", "ES256"],
        "issuer": issuer,
        "options": decode_options,
    }
    if audience:
        decode_kwargs["audience"] = audience

    try:
        claims: dict = jwt.decode(token, **decode_kwargs)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail="Token has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as exc:
        logger.warning("JWT decode failed: %s", exc)
        raise HTTPException(
            status_code=401,
            detail="Invalid token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Role check — support flat "roles" list or Keycloak's realm_access.roles
    roles: list[str] = []
    if isinstance(claims.get("roles"), list):
        roles = claims["roles"]
    elif isinstance(claims.get("realm_access"), dict):
        roles = claims["realm_access"].get("roles", [])

    if required_role not in roles:
        raise HTTPException(
            status_code=403,
            detail=f"Role '{required_role}' required.",
        )
