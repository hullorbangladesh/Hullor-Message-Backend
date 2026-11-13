import hashlib
import hmac
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_safe

from .handler import handle_event

log = logging.getLogger(__name__)

# Messages are processed here, so the webhook can answer Meta within milliseconds.
executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="bot")


@require_safe
def health(request):
    """For UptimeRobot / Render. Deliberately does not touch the database."""
    return HttpResponse("ok", content_type="text/plain")


def _valid_signature(request):
    secret = settings.META_APP_SECRET
    header = request.headers.get("X-Hub-Signature-256", "")
    if not secret or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len("sha256="):], expected)


@csrf_exempt
def webhook(request):
    if request.method == "GET":
        # Meta calls this once when you save the webhook in the developer dashboard.
        token = request.GET.get("hub.verify_token", "")
        expected = settings.VERIFY_TOKEN
        if (
            request.GET.get("hub.mode") == "subscribe"
            and expected
            and hmac.compare_digest(token.encode(), expected.encode())
        ):
            return HttpResponse(request.GET.get("hub.challenge", ""), content_type="text/plain")
        return HttpResponseForbidden("verification failed")

    if request.method == "POST":
        if not _valid_signature(request):
            return HttpResponseForbidden("bad signature")
        try:
            payload = json.loads(request.body)
        except ValueError:
            return HttpResponseBadRequest("invalid json")
        if payload.get("object") == "page":
            for entry in payload.get("entry", []):
                for event in entry.get("messaging", []):
                    executor.submit(handle_event, event)
        return HttpResponse("EVENT_RECEIVED", content_type="text/plain")

    return HttpResponse(status=405)
