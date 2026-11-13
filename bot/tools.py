"""Functions Gemini can call. Every number the customer sees (price, total) comes from here, never from the model."""

import logging
import re
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from shop.models import Conversation, Order, OrderItem, Product

from . import messenger

log = logging.getLogger(__name__)

BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def _int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def normalize_phone(raw):
    s = re.sub(r"[\s\-()]", "", str(raw or "")).translate(BN_DIGITS)
    if s.startswith("+880"):
        s = "0" + s[4:]
    elif s.startswith("880"):
        s = "0" + s[3:]
    return s if re.fullmatch(r"01[3-9][0-9]{8}", s) else None


def _product_json(p):
    return {
        "id": p.id,
        "name": p.name,
        "name_bn": p.name_bn,
        "category": p.category,
        "price_bdt": p.price,
        "in_stock": p.stock > 0,
        "stock_left": p.stock if 0 < p.stock <= 5 else None,
        "details": p.description[:250],
        "has_photo": bool(p.image_url),
    }


def search_products(conv, args):
    query = str(args.get("query", "")).lower().strip()
    limit = min(max(_int(args.get("max_results"), 5), 1), 8)
    terms = list(dict.fromkeys(t for t in re.split(r"[\s,]+", query) if len(t) >= 2))[:6]
    if not terms:
        return {"products": [], "note": "Empty query. Search with product words."}

    q = Q()
    for t in terms:
        q |= (
            Q(name__icontains=t)
            | Q(name_bn__icontains=t)
            | Q(keywords__icontains=t)
            | Q(category__icontains=t)
        )
    candidates = list(Product.objects.filter(is_active=True).filter(q)[:300])

    def score(p):
        in_name = f"{p.name} {p.name_bn}".lower()
        in_meta = f"{p.keywords} {p.category}".lower()
        s = 0.0
        for t in terms:
            if t in in_name:
                s += 3
            if t in in_meta:
                s += 1
        return s + (0.5 if p.stock > 0 else 0)

    candidates.sort(key=score, reverse=True)
    return {
        "total_matches": len(candidates),
        "products": [_product_json(p) for p in candidates[:limit]],
    }


def list_categories(conv, args):
    rows = (
        Product.objects.filter(is_active=True)
        .exclude(category="")
        .values("category")
        .annotate(n=Count("id"))
        .order_by("-n")[:40]
    )
    return {"categories": [{"name": r["category"], "products": r["n"]} for r in rows]}


def send_product_photo(conv, args):
    product = Product.objects.filter(pk=_int(args.get("product_id")), is_active=True).first()
    if not product:
        return {"sent": False, "error": "Product not found"}
    if not product.image_url:
        return {"sent": False, "error": "This product has no photo"}
    return {"sent": messenger.send_image(conv.psid, product.image_url)}


def request_human(conv, args):
    conv.human_handoff = True
    conv.handoff_at = timezone.now()
    conv.handoff_reason = str(args.get("reason", ""))[:300]
    conv.save(update_fields=["human_handoff", "handoff_at", "handoff_reason"])
    return {"ok": True, "note": "A team member has been alerted and will reply soon."}


def _order_json(order):
    items = [
        {
            "name": i.product_name,
            "quantity": i.quantity,
            "unit_price": i.unit_price,
            "line_total": i.unit_price * i.quantity,
        }
        for i in order.items.all()
    ]
    return {
        "ok": True,
        "order_id": order.id,
        "items": items,
        "subtotal": order.subtotal,
        "delivery_charge": order.delivery_charge,
        "total": order.total,
        "payment": "Cash on Delivery",
    }


def place_order(conv, args):
    name = str(args.get("customer_name", "")).strip()
    address = str(args.get("address", "")).strip()
    area = str(args.get("area", "")).strip()
    phone = normalize_phone(args.get("phone"))
    raw_items = args.get("items") or []

    if len(name) < 2:
        return {"ok": False, "error": "Customer name is missing."}
    if not phone:
        return {"ok": False, "error": "Phone number is invalid. Ask for a Bangladeshi mobile number like 01XXXXXXXXX."}
    if len(address) < 10:
        return {"ok": False, "error": "Address is too short. Ask for the full address (area, road, house, district)."}
    if area not in (Order.Area.DHAKA, Order.Area.OUTSIDE):
        return {"ok": False, "error": "area must be 'dhaka' or 'outside_dhaka'. Ask the customer."}
    if not isinstance(raw_items, list) or not raw_items:
        return {"ok": False, "error": "No items given."}

    wanted = {}
    for it in raw_items[:20]:
        if not isinstance(it, dict):
            continue
        pid, qty = _int(it.get("product_id")), _int(it.get("quantity"), 1)
        if pid <= 0 or qty < 1 or qty > 20:
            return {"ok": False, "error": "Invalid product_id or quantity (1-20)."}
        wanted[pid] = wanted.get(pid, 0) + qty
    if not wanted:
        return {"ok": False, "error": "No valid items."}

    delivery = settings.DELIVERY_CHARGE_DHAKA if area == Order.Area.DHAKA else settings.DELIVERY_CHARGE_OUTSIDE

    with transaction.atomic():
        products = {p.id: p for p in Product.objects.select_for_update().filter(id__in=wanted, is_active=True)}
        for pid, qty in wanted.items():
            p = products.get(pid)
            if not p:
                return {"ok": False, "error": f"Product id {pid} not found. Search again to get the right id."}
            if p.stock < qty:
                left = f"only {p.stock} left" if p.stock else "out of stock"
                return {"ok": False, "error": f"'{p.name}' is {left}."}

        subtotal = sum(products[pid].price * qty for pid, qty in wanted.items())
        total = subtotal + delivery

        # Same customer sending the same order again within 2 minutes = a double click, return the first one.
        recent = Order.objects.filter(
            psid=conv.psid, phone=phone, total=total, created_at__gte=timezone.now() - timedelta(minutes=2)
        ).first()
        if recent:
            data = _order_json(recent)
            data["note"] = "This order was already placed a moment ago."
            return data

        order = Order.objects.create(
            psid=conv.psid,
            customer_name=name[:120],
            phone=phone,
            address=address,
            area=area,
            subtotal=subtotal,
            delivery_charge=delivery,
            total=total,
        )
        for pid, qty in wanted.items():
            p = products[pid]
            OrderItem.objects.create(
                order=order, product=p, product_name=p.name, unit_price=p.price, quantity=qty
            )
            Product.objects.filter(pk=pid).update(stock=F("stock") - qty)

    log.info("Order #%s placed (total %s)", order.id, total)
    return _order_json(order)


TOOLS = {
    "search_products": search_products,
    "list_categories": list_categories,
    "send_product_photo": send_product_photo,
    "request_human": request_human,
    "place_order": place_order,
}


def execute_tool(conv, name, args):
    fn = TOOLS.get(name)
    if not fn:
        return {"error": f"Unknown tool {name}"}
    try:
        return fn(conv, args or {})
    except Exception:
        log.exception("Tool %s failed", name)
        return {"error": "Internal error, try again or hand over to a human."}
