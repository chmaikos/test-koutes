"""Microsoft Entra ID JWT validation.

The SPA acquires an access token for the API's exposed scope and sends it as a
Bearer token. We validate the signature against the tenant's JWKS, then check
issuer + audience, and finally extract the App Role from the `roles` claim to
upsert a local `users` row.
"""
from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import HTTPException, status
from jose import jwt
from jose.exceptions import JWTError

from app.config import Settings, get_settings
from app.models.users import UserRole

# JWKS is small (a handful of keys) and rotates rarely; cache for an hour.
_JWKS_CACHE: dict[str, Any] = {"fetched_at": 0.0, "keys": None}
_JWKS_TTL_SECONDS = 3600

_ROLE_PRIORITY: dict[str, UserRole] = {
    "admin": UserRole.admin,
    "operator": UserRole.operator,
    "viewer": UserRole.viewer,
}


class TokenClaims:
    """Light wrapper around the validated JWT payload."""

    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        self.oid: str = str(payload.get("oid") or payload.get("sub") or "")
        self.email: str = (
            payload.get("preferred_username")
            or payload.get("upn")
            or payload.get("email")
            or ""
        )
        self.name: str = payload.get("name") or self.email
        roles_claim = payload.get("roles") or []
        if isinstance(roles_claim, str):
            roles_claim = [roles_claim]
        self.roles: list[str] = [str(r).lower() for r in roles_claim]

    def best_role(self) -> UserRole:
        for candidate in (UserRole.admin, UserRole.operator, UserRole.viewer):
            if candidate.value in self.roles:
                return candidate
        return UserRole.viewer


async def _fetch_jwks(settings: Settings) -> list[dict[str, Any]]:
    now = time.time()
    if _JWKS_CACHE["keys"] is not None and (now - _JWKS_CACHE["fetched_at"]) < _JWKS_TTL_SECONDS:
        return _JWKS_CACHE["keys"]
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(settings.jwks_url)
        resp.raise_for_status()
        data = resp.json()
    keys = data.get("keys") or []
    _JWKS_CACHE["keys"] = keys
    _JWKS_CACHE["fetched_at"] = now
    return keys


def _select_key(keys: list[dict[str, Any]], kid: str) -> dict[str, Any] | None:
    for key in keys:
        if key.get("kid") == kid:
            return key
    return None


async def validate_access_token(token: str) -> TokenClaims:
    settings = get_settings()
    if not settings.auth_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="auth not configured",
        )
    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"invalid token header: {exc}",
        ) from exc

    kid = unverified_header.get("kid")
    if not kid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing kid"
        )

    keys = await _fetch_jwks(settings)
    key = _select_key(keys, kid)
    if key is None:
        # JWKS may have rotated; force a refresh once.
        _JWKS_CACHE["keys"] = None
        keys = await _fetch_jwks(settings)
        key = _select_key(keys, kid)
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="signing key not found"
        )

    audience_candidates = {settings.entra_api_audience, settings.entra_client_id}
    audience_candidates.discard("")

    last_error: Exception | None = None
    for issuer in (settings.issuer, settings.issuer_v1):
        try:
            payload = jwt.decode(
                token,
                key,
                algorithms=[unverified_header.get("alg") or "RS256"],
                audience=list(audience_candidates) if audience_candidates else None,
                issuer=issuer,
                options={"verify_at_hash": False},
            )
            return TokenClaims(payload)
        except JWTError as exc:
            last_error = exc
            continue

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"invalid token: {last_error}",
    )
