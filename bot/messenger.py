"""Thin wrapper around the Messenger Send API. Never logs the access token."""

import logging

import requests
from django.conf import settings

log = logging.getLogger(__name__)

MAX_CHARS = 1800  # Messenger limit is 2000 per message


def _base():
    return f"https://graph.facebook.com/{settings.GRAPH_API_VERSION}"


def _post(path, payload):
    token = settings.PAGE_ACCESS_TOKEN
    if not token:
        log.error("PAGE_ACCESS_TOKEN is not set; cannot send message")
        return False
    try:
        resp = requests.post(
            f"{_base()}/{path}",
            params={"access_token": token},
            json=payload,
            timeout=15,
        )
    except requests.RequestException as exc:
        # Only the exception type is logged: the message could contain the URL with the token.
        log.error("Messenger request failed: %s", type(exc).__name__)
        return False
    if resp.status_code >= 400:
        log.error("Messenger API error %s: %s", resp.status_code, resp.text[:300])
        return False
    return True


def _chunks(text, limit=MAX_CHARS):
    text = (text or "").strip()
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        yield text[:cut].strip()
        text = text[cut:].strip()
    if text:
        yield text


def send_text(psid, text):
    ok = True
    for chunk in _chunks(text):
        ok &= _post(
            "me/messages",
            {
                "recipient": {"id": psid},
                "messaging_type": "RESPONSE",
                "message": {"text": chunk},
            },
        )
    return ok


def send_image(psid, url):
    return _post(
        "me/messages",
        {
            "recipient": {"id": psid},
            "messaging_type": "RESPONSE",
            "message": {"attachment": {"type": "image", "payload": {"url": url, "is_reusable": True}}},
        },
    )


def send_action(psid, action="typing_on"):
    return _post("me/messages", {"recipient": {"id": psid}, "sender_action": action})


def get_profile_name(psid):
    """Best-effort customer name (shown in the admin panel). Returns '' on any problem."""
    token = settings.PAGE_ACCESS_TOKEN
    if not token:
        return ""
    try:
        resp = requests.get(
            f"{_base()}/{psid}",
            params={"fields": "first_name,last_name", "access_token": token},
            timeout=10,
        )
        if resp.status_code != 200:
            return ""
        data = resp.json()
        return f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
    except (requests.RequestException, ValueError):
        return ""
