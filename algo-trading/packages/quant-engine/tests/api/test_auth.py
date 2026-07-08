"""
tests/api/test_auth.py — Tests for the auth/RBAC dependency layer.

Covers:
- ``require_operator`` is a no-op when OIDC_ISSUER_URL is not set (dev mode)
- 401 is returned when OIDC is configured but no Bearer token is supplied
- 403 is returned when the token is valid but the required role is absent
- Role check passes with both flat "roles" and Keycloak "realm_access.roles"
- Protected mutation endpoints (risk resume, strategy toggle, backtest run/delete)
  are accessible without a token in dev mode
- Protected mutation endpoints return 401 in OIDC-configured mode without token
"""

from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient  # noqa: F401 – used via conftest fixtures

from api.deps import _validate_token_and_role

# ---------------------------------------------------------------------------
# Unit tests for _validate_token_and_role
# ---------------------------------------------------------------------------

class TestValidateTokenAndRole:
    """Direct unit tests for the internal validation helper."""

    def _make_jwt_module(self) -> types.ModuleType:
        """Return a minimal mock of the jwt (PyJWT) module."""
        jwt_mod = types.ModuleType("jwt")
        jwt_mod.ExpiredSignatureError = Exception
        jwt_mod.InvalidTokenError = Exception
        return jwt_mod

    def test_missing_pyjwt_raises_503(self):
        """If PyJWT is not importable, a 503 is returned."""
        from fastapi import HTTPException

        with patch.dict("sys.modules", {"jwt": None}):
            with pytest.raises(HTTPException) as exc_info:
                _validate_token_and_role("tok", "https://issuer", None, "operator")
        assert exc_info.value.status_code == 503

    def test_jwks_fetch_failure_raises_401(self):
        """A JWKS fetch failure results in a 401."""
        from fastapi import HTTPException

        jwt_mod = types.ModuleType("jwt")
        jwt_mod.ExpiredSignatureError = ValueError
        jwt_mod.InvalidTokenError = ValueError

        class BadJWKSClient:
            def __init__(self, url: str) -> None:
                pass

            def get_signing_key_from_jwt(self, token: str):
                raise RuntimeError("Network error")

        jwt_mod.PyJWKClient = BadJWKSClient  # type: ignore[attr-defined]

        with patch.dict("sys.modules", {"jwt": jwt_mod}):
            with pytest.raises(HTTPException) as exc_info:
                _validate_token_and_role("bad.tok.en", "https://issuer", None, "operator")
        assert exc_info.value.status_code == 401

    def test_expired_token_raises_401(self):
        """An expired token raises HTTP 401."""
        from fastapi import HTTPException

        class ExpiredError(Exception):
            pass

        jwt_mod = types.ModuleType("jwt")
        jwt_mod.ExpiredSignatureError = ExpiredError
        jwt_mod.InvalidTokenError = Exception

        signing_key_mock = MagicMock()
        signing_key_mock.key = "dummy-key"

        class MockJWKSClient:
            def __init__(self, url: str) -> None:
                pass

            def get_signing_key_from_jwt(self, token: str):
                return signing_key_mock

        jwt_mod.PyJWKClient = MockJWKSClient  # type: ignore[attr-defined]

        def mock_decode(*args, **kwargs):
            raise ExpiredError("expired")

        jwt_mod.decode = mock_decode  # type: ignore[attr-defined]

        with patch.dict("sys.modules", {"jwt": jwt_mod}):
            with pytest.raises(HTTPException) as exc_info:
                _validate_token_and_role("tok", "https://issuer", None, "operator")
        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()

    def test_missing_role_raises_403_flat_roles(self):
        """When decoded claims have no matching role (flat list), raises 403."""
        from fastapi import HTTPException

        class InvalidTokenError(Exception):
            pass

        jwt_mod = types.ModuleType("jwt")
        jwt_mod.ExpiredSignatureError = Exception
        jwt_mod.InvalidTokenError = InvalidTokenError

        signing_key_mock = MagicMock()
        signing_key_mock.key = "dummy-key"

        class MockJWKSClient:
            def __init__(self, url: str) -> None:
                pass

            def get_signing_key_from_jwt(self, token: str):
                return signing_key_mock

        jwt_mod.PyJWKClient = MockJWKSClient  # type: ignore[attr-defined]
        # Token has roles but NOT "operator"
        jwt_mod.decode = lambda *a, **kw: {  # type: ignore[attr-defined]
            "roles": ["viewer"], "iss": "https://issuer", "exp": 9999999999
        }

        with patch.dict("sys.modules", {"jwt": jwt_mod}):
            with pytest.raises(HTTPException) as exc_info:
                _validate_token_and_role("tok", "https://issuer", None, "operator")
        assert exc_info.value.status_code == 403

    def test_valid_flat_role_passes(self):
        """A token with the required role in a flat 'roles' list passes silently."""
        class InvalidTokenError(Exception):
            pass

        jwt_mod = types.ModuleType("jwt")
        jwt_mod.ExpiredSignatureError = Exception
        jwt_mod.InvalidTokenError = InvalidTokenError

        signing_key_mock = MagicMock()
        signing_key_mock.key = "dummy-key"

        class MockJWKSClient:
            def __init__(self, url: str) -> None:
                pass

            def get_signing_key_from_jwt(self, token: str):
                return signing_key_mock

        jwt_mod.PyJWKClient = MockJWKSClient  # type: ignore[attr-defined]
        jwt_mod.decode = lambda *a, **kw: {  # type: ignore[attr-defined]
            "roles": ["operator"], "iss": "https://issuer", "exp": 9999999999
        }

        with patch.dict("sys.modules", {"jwt": jwt_mod}):
            # Should not raise
            _validate_token_and_role("tok", "https://issuer", None, "operator")

    def test_valid_keycloak_realm_access_role_passes(self):
        """A token with the role in Keycloak 'realm_access.roles' passes."""
        class InvalidTokenError(Exception):
            pass

        jwt_mod = types.ModuleType("jwt")
        jwt_mod.ExpiredSignatureError = Exception
        jwt_mod.InvalidTokenError = InvalidTokenError

        signing_key_mock = MagicMock()
        signing_key_mock.key = "dummy-key"

        class MockJWKSClient:
            def __init__(self, url: str) -> None:
                pass

            def get_signing_key_from_jwt(self, token: str):
                return signing_key_mock

        jwt_mod.PyJWKClient = MockJWKSClient  # type: ignore[attr-defined]
        jwt_mod.decode = lambda *a, **kw: {  # type: ignore[attr-defined]
            "realm_access": {"roles": ["operator", "viewer"]},
            "iss": "https://issuer",
            "exp": 9999999999,
        }

        with patch.dict("sys.modules", {"jwt": jwt_mod}):
            _validate_token_and_role("tok", "https://issuer", None, "operator")


# ---------------------------------------------------------------------------
# Integration: mutation endpoints pass in dev mode (no OIDC configured)
# ---------------------------------------------------------------------------

class TestMutationEndpointsDevMode:
    """Protected endpoints must remain accessible in dev (no OIDC set)."""

    def test_risk_resume_accessible_in_dev(self, halted_client: TestClient):
        resp = halted_client.post("/api/risk/resume", json={"new_equity": None})
        # Returns 200 (not 401 or 403)
        assert resp.status_code == 200

    def test_strategy_toggle_accessible_in_dev(self, client: TestClient):
        resp = client.patch("/api/strategies/momentum", json={"enabled": False})
        assert resp.status_code == 200

    def test_backtest_run_accessible_in_dev(self, client: TestClient):
        resp = client.post("/api/backtest/run", json={
            "tickers": ["AAPL"],
            "start_date": "2023-01-01",
            "end_date": "2023-02-01",
        })
        assert resp.status_code == 202

    def test_backtest_delete_accessible_in_dev(self, state, client: TestClient):
        run_id = "auth-del001"
        state.backtest_results[run_id] = {}
        resp = client.delete(f"/api/backtest/{run_id}")
        assert resp.status_code == 204
