"""Turns one Messenger event into a reply. Runs in a background thread so the webhook can answer Meta instantly."""

import logging
import random
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta

from django.db import connections
from django.utils import timezone

from shop.models import Conversation, ProcessedMessage

from . import gemini, messenger

log = logging.getLogger(__name__)

ATTACHMENT_REPLY = "ছবি/ফাইলের জন্য ধন্যবাদ! 😊 কোন পণ্যটি লাগবে সেটা লিখে জানালে আমি সাহায্য করছি।"

_locks_guard = threading.Lock()
_locks = {}
_recent = defaultdict(deque)
RATE_WINDOW_SECONDS = 60
RATE_MAX_MESSAGES = 15  # protects the free Gemini quota from spam


def _lock_for(psid):
    with _locks_guard:
        return _locks.setdefault(psid, threading.Lock())


def _rate_limited(psid):
    now = time.monotonic()
    if len(_recent) > 5000:
        _recent.clear()
    window = _recent[psid]
    while window and now - window[0] > RATE_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= RATE_MAX_MESSAGES:
        return True
    window.append(now)
    return False


def process_message(psid, mid, text, has_attachment=False):
    if mid:
        _, created = ProcessedMessage.objects.get_or_create(mid=mid)
        if not created:
            return  # duplicate delivery from Meta
        if random.random() < 0.01:
            ProcessedMessage.objects.filter(created_at__lt=timezone.now() - timedelta(days=7)).delete()

    if _rate_limited(psid):
        log.warning("Rate limit hit for a user, message ignored")
        return

    with _lock_for(psid):  # one message at a time per customer
        conv, created = Conversation.objects.get_or_create(psid=psid)
        if created:
            conv.customer_name = messenger.get_profile_name(psid)
        conv.last_user_message = (text or "[attachment]")[:500]
        conv.last_message_at = timezone.now()
        conv.save()

        if conv.handoff_active():
            return  # a human is handling this chat
        if conv.human_handoff:  # handoff expired, bot takes over again
            conv.human_handoff = False
            conv.save(update_fields=["human_handoff"])

        if not text:
            if has_attachment:
                messenger.send_text(psid, ATTACHMENT_REPLY)
            return

        messenger.send_action(psid, "typing_on")
        try:
            reply = gemini.generate_reply(conv, text)
        except Exception:
            log.exception("Gemini failed")
            reply = gemini.FALLBACK_REPLY
        messenger.send_text(psid, reply)


def _handle(event):
    sender = (event.get("sender") or {}).get("id")
    if not sender:
        return
    message = event.get("message")
    postback = event.get("postback")
    if message:
        if message.get("is_echo"):
            return
        process_message(
            sender,
            message.get("mid"),
            (message.get("text") or "").strip(),
            bool(message.get("attachments")),
        )
    elif postback:
        payload = postback.get("payload") or ""
        text = "হ্যালো" if payload == "GET_STARTED" else (postback.get("title") or payload)
        process_message(sender, None, text.strip(), False)
    # other events (delivery/read receipts) are ignored


def handle_event(event):
    """Entry point used by the thread pool."""
    try:
        _handle(event)
    except Exception:
        log.exception("Failed to process Messenger event")
    finally:
        connections.close_all()
