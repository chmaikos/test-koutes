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


def send_alert_email(*, subject: str, html_body: str) -> bool:
    settings = get_settings()
    if not settings.graph_configured:
        logger.info("graph not configured; skipping alert email")
        return False
    token = _acquire_token()
    if token is None:
        return False
    url = f"{_GRAPH_BASE}/users/{settings.alert_email_from}/sendMail"
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [
                {"emailAddress": {"address": addr}}
                for addr in settings.alert_email_to_list
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
            logger.warning("graph sendMail failed: %s %s", resp.status_code, resp.text)
            return False
        return True
    except httpx.HTTPError as exc:
        logger.warning("graph sendMail error: %s", exc)
        return False
