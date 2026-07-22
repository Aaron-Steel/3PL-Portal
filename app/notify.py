"""Outbound notifications. The app holds no SMTP/Graph creds — it POSTs the reset link to
an n8n webhook, which sends the email via its Outlook node (feeds@macgeargroup.com), the
same pattern as the birthday notifier. When N8N_RESET_WEBHOOK_URL is unset (local dev) the
link is logged to the console instead, so ./run.ps1 works fully offline. Stdlib only."""
import json
import logging
import os
import urllib.request

log = logging.getLogger("threepl.notify")

WEBHOOK_URL = os.environ.get("N8N_RESET_WEBHOOK_URL", "")
# Shared secret sent as a header for the n8n Webhook node's header-auth. Reuses SYNC_TOKEN
# if a dedicated one isn't set, so a single secret can cover both integration directions.
WEBHOOK_TOKEN = os.environ.get("N8N_WEBHOOK_TOKEN", "") or os.environ.get("SYNC_TOKEN", "")


def send_reset_email(to: str, reset_url: str) -> None:
    """Ask n8n to email a password reset / set-password link. Never raises to the caller —
    a delivery failure must not reveal (via a 500) whether the address exists."""
    if not WEBHOOK_URL:
        log.warning("N8N_RESET_WEBHOOK_URL unset — reset link for %s: %s", to, reset_url)
        return
    payload = json.dumps({"to": to, "reset_url": reset_url}).encode()
    req = urllib.request.Request(WEBHOOK_URL, data=payload, method="POST",
                                 headers={"Content-Type": "application/json"})
    if WEBHOOK_TOKEN:
        req.add_header("X-Sync-Token", WEBHOOK_TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as e:  # noqa: BLE001 — log and swallow; see docstring
        log.error("reset email webhook failed for %s: %s", to, e)
