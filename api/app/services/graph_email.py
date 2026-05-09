"""Send mail via Microsoft Graph using the client-credentials flow.

We do not run this on the request path; alerts call us from a background task
or the scheduler. Failures are logged and swallowed so the alert row is still
persisted regardless.
"""
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


def send_alert_email(
    *,
    subject: str,
    html_body: str,
    to: list[str] | None = None,
) -> tuple[bool, str | None]:
    """Send a single alert email through Graph.

    ``to`` is the explicit recipient list; if omitted we fall back to the
    static ``ALERT_EMAIL_TO`` for backwards compatibility with any caller
    that hasn't been updated to use :mod:`app.services.alert_recipients`.

    Returns ``(ok, error)`` so the caller can log a structured reason in
    the alert_notifications audit table; ``error`` is ``None`` on success.
    """
    settings = get_settings()
    if not settings.graph_configured:
        return False, "graph not configured"
    recipients = to if to is not None else settings.alert_email_to_list
    if not recipients:
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
                {"emailAddress": {"address": addr}} for addr in recipients
            ],
        },
        "saveToSentItems": False,
    }
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
