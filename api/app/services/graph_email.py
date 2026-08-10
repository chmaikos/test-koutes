"""Generic Microsoft Graph email transport using client credentials."""
from __future__ import annotations

import logging

import httpx
import msal

from app.config import get_settings

logger = logging.getLogger("warehouse.graph")

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _build_msal_client() -> msal.ConfidentialClientApplication | None:
    settings = get_settings()
    if not settings.graph_configured:
        return None
    authority = f"https://login.microsoftonline.com/{settings.entra_tenant_id}"
    return msal.ConfidentialClientApplication(
        client_id=settings.entra_client_id,
        authority=authority,
        client_credential=settings.entra_client_secret,
    )


def _acquire_token() -> str | None:
    app = _build_msal_client()
    if app is None:
        return None
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        logger.warning("graph token acquisition failed: %s", result.get("error_description"))
        return None
    return result["access_token"]


def send_email(
    *,
    subject: str,
    html_body: str,
    to: list[str],
    text_body: str | None = None,
) -> tuple[bool, str | None]:
    """Send one message and return a structured transport result."""
    settings = get_settings()
    if not settings.graph_configured:
        return False, "graph not configured"
    if not to:
        return False, "no recipients"
    token = _acquire_token()
    if token is None:
        return False, "token acquisition failed"
    url = f"{_GRAPH_BASE}/users/{settings.alert_email_from}/sendMail"
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [
                {"emailAddress": {"address": addr}} for addr in to
            ],
        },
        "saveToSentItems": False,
    }
    # Graph's sendMail endpoint accepts one body. ``text_body`` remains part
    # of the generic transport contract for audit/alternate transports; Graph
    # receives the HTML version.
    _ = text_body
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code >= 300:
            error = f"http {resp.status_code}: {resp.text[:200]}"
            logger.warning("graph sendMail failed: %s", error)
            return False, error
        return True, None
    except httpx.HTTPError as exc:
        error = f"http error: {exc}"
        logger.warning("graph sendMail error: %s", exc)
        return False, error


def send_alert_email(
    *,
    subject: str,
    html_body: str,
    to: list[str] | None = None,
) -> tuple[bool, str | None]:
    """Compatibility wrapper for alert and productivity callers."""
    settings = get_settings()
    recipients = to if to is not None else settings.alert_email_to_list
    return send_email(subject=subject, html_body=html_body, to=recipients)


__all__ = ["send_alert_email", "send_email"]
