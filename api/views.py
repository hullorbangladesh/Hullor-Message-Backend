"""JSON API used by the HTML admin panel. Protected by a Bearer token (ADMIN_API_TOKEN)."""

import hmac
import json
from functools import wraps

from django.conf import settings
from django.db.models import Count, Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from shop.models import Conversation, Order, Product


def token_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        expected = settings.ADMIN_API_TOKEN
        if len(expected) < 20:
            return JsonResponse(
                {"error": "ADMIN_API_TOKEN is missing or too short (use at least 20 random characters)."},
                status=503,
            )
        header = request.headers.get("Authorization", "")
        token = header[7:] if header.startswith("Bearer ") else ""
        if not hmac.compare_digest(token.encode(), expected.encode()):
            return JsonResponse({"error": "unauthorized"}, status=401)
        return view(request, *args, **kwargs)

    return csrf_exempt(wrapper)


def _body(request):
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _bad(message, status=400):
    return JsonResponse({"error": message}, status=status)


def _limit(request, default, maximum):
    try:
        return min(max(int(request.GET.get("limit", default)), 1), maximum)
    except ValueError:
        return default


def order_json(o):
    return {
        "id": o.id,
        "customer_name": o.customer_name,
        "phone": o.phone,
        "address": o.address,
        "area": o.area,
        "area_label": o.get_area_display(),
        "subtotal": o.subtotal,
        "delivery_charge": o.delivery_charge,
        "total": o.total,
        "status": o.status,
        "created_at": o.created_at.isoformat(),
        "items": [
            {"name": i.product_name, "quantity": i.quantity, "unit_price": i.unit_price}
            for i in o.items.all()
        ],
    }


def chat_json(c):
    return {
        "id": c.id,
        "psid": c.psid,
        "customer_name": c.customer_name,
        "last_user_message": c.last_user_message,
        "last_message_at": c.last_message_at.isoformat(),
        "human_handoff": c.human_handoff,
        "handoff_active": c.handoff_active(),
        "handoff_reason": c.handoff_reason,
        "handoff_at": c.handoff_at.isoformat() if c.handoff_at else None,
    }


def product_json(p):
    return {
        "id": p.id,
        "sku": p.sku,
        "name": p.name,
        "name_bn": p.name_bn,
        "category": p.category,
        "price": p.price,
        "stock": p.stock,
        "is_active": p.is_active,
    }


@token_required
def ping(request):
    return JsonResponse({"ok": True})


@token_required
def orders_list(request):
    if request.method != "GET":
        return _bad("method not allowed", 405)
    qs = Order.objects.prefetch_related("items")
    status = request.GET.get("status")
    if status:
        qs = qs.filter(status=status)
    counts = dict(Order.objects.values_list("status").annotate(n=Count("id")))
    return JsonResponse(
        {"orders": [order_json(o) for o in qs[: _limit(request, 100, 300)]], "counts": counts}
    )


@token_required
def order_detail(request, pk):
    try:
        order = Order.objects.prefetch_related("items").get(pk=pk)
    except Order.DoesNotExist:
        return _bad("not found", 404)
    if request.method == "GET":
        return JsonResponse(order_json(order))
    if request.method == "PATCH":
        data = _body(request)
        if data is None:
            return _bad("invalid json")
        status = data.get("status")
        if status not in Order.Status.values:
            return _bad("invalid status")
        if order.status == Order.Status.CANCELLED and status != Order.Status.CANCELLED:
            return _bad("cancelled orders cannot be reopened (stock was already returned)")
        order.status = status
        order.save()
        return JsonResponse(order_json(order))
    return _bad("method not allowed", 405)


@token_required
def chats_list(request):
    if request.method != "GET":
        return _bad("method not allowed", 405)
    qs = Conversation.objects.all()
    if request.GET.get("handoff") == "1":
        qs = qs.filter(human_handoff=True)
    return JsonResponse({"chats": [chat_json(c) for c in qs[: _limit(request, 100, 300)]]})


@token_required
def chat_detail(request, pk):
    try:
        chat = Conversation.objects.get(pk=pk)
    except Conversation.DoesNotExist:
        return _bad("not found", 404)
    if request.method == "GET":
        return JsonResponse(chat_json(chat))
    if request.method == "PATCH":
        data = _body(request)
        if data is None or not isinstance(data.get("human_handoff"), bool):
            return _bad("send {\"human_handoff\": true|false}")
        chat.human_handoff = data["human_handoff"]
        if chat.human_handoff:
            chat.handoff_at = timezone.now()
        else:
            chat.handoff_at = None
            chat.handoff_reason = ""
        chat.save(update_fields=["human_handoff", "handoff_at", "handoff_reason"])
        return JsonResponse(chat_json(chat))
    return _bad("method not allowed", 405)


@token_required
def products_list(request):
    if request.method != "GET":
        return _bad("method not allowed", 405)
    qs = Product.objects.all()
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(name__icontains=q) | Q(name_bn__icontains=q) | Q(sku__icontains=q) | Q(keywords__icontains=q)
        )
    total = qs.count()
    return JsonResponse(
        {"total": total, "products": [product_json(p) for p in qs[: _limit(request, 50, 200)]]}
    )


@token_required
def product_detail(request, pk):
    try:
        product = Product.objects.get(pk=pk)
    except Product.DoesNotExist:
        return _bad("not found", 404)
    if request.method == "GET":
        return JsonResponse(product_json(product))
    if request.method == "PATCH":
        data = _body(request)
        if data is None:
            return _bad("invalid json")
        update = []
        for field in ("price", "stock"):
            if field in data:
                value = data[field]
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    return _bad(f"{field} must be a whole number >= 0")
                setattr(product, field, value)
                update.append(field)
        if "is_active" in data:
            if not isinstance(data["is_active"], bool):
                return _bad("is_active must be true or false")
            product.is_active = data["is_active"]
            update.append("is_active")
        if update:
            product.save(update_fields=update + ["updated_at"])
        return JsonResponse(product_json(product))
    return _bad("method not allowed", 405)
