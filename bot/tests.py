import hashlib
import hmac
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from shop.importer import import_products_csv
from shop.models import Conversation, Order, Product

from . import gemini, handler, tools

SECRET = "app-secret-for-tests"
TOKEN = "t" * 32


def sign(body: bytes, secret=SECRET):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def make_products():
    shirt = Product.objects.create(
        sku="s1", name="Cotton Polo Shirt", name_bn="কটন পোলো শার্ট", category="Clothing",
        keywords="polo tshirt", price=650, stock=5,
    )
    speaker = Product.objects.create(
        sku="e1", name="Bluetooth Speaker", category="Electronics", keywords="speaker", price=1450, stock=1,
    )
    return shirt, speaker


class HealthTests(TestCase):
    def test_health_get_head_and_root(self):
        self.assertEqual(self.client.get("/health").content, b"ok")
        self.assertEqual(self.client.head("/health").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_health_makes_no_db_queries(self):
        with self.assertNumQueries(0):
            self.client.get("/health")


@override_settings(VERIFY_TOKEN="verify-me", META_APP_SECRET=SECRET)
class WebhookTests(TestCase):
    def test_verification_ok(self):
        r = self.client.get("/webhook", {"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "12345"})
        self.assertEqual((r.status_code, r.content), (200, b"12345"))

    def test_verification_wrong_token(self):
        r = self.client.get("/webhook", {"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"})
        self.assertEqual(r.status_code, 403)

    @override_settings(VERIFY_TOKEN="")
    def test_verification_refused_when_token_not_configured(self):
        r = self.client.get("/webhook", {"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"})
        self.assertEqual(r.status_code, 403)

    def test_post_rejects_bad_signature(self):
        body = json.dumps({"object": "page", "entry": []}).encode()
        for headers in ({}, {"HTTP_X_HUB_SIGNATURE_256": sign(body, "wrong")}):
            r = self.client.post("/webhook", body, content_type="application/json", **headers)
            self.assertEqual(r.status_code, 403)

    def test_post_valid_signature_queues_each_event(self):
        payload = {"object": "page", "entry": [{"messaging": [
            {"sender": {"id": "1"}, "message": {"mid": "m1", "text": "hi"}},
            {"sender": {"id": "2"}, "message": {"mid": "m2", "text": "hello"}},
        ]}]}
        body = json.dumps(payload).encode()
        with patch("bot.views.executor") as ex:
            r = self.client.post("/webhook", body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sign(body))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(ex.submit.call_count, 2)


@override_settings(DELIVERY_CHARGE_DHAKA=80, DELIVERY_CHARGE_OUTSIDE=130)
class ToolTests(TestCase):
    def setUp(self):
        self.shirt, self.speaker = make_products()
        self.conv = Conversation.objects.create(psid="u1")

    def order(self, **over):
        args = {
            "customer_name": "Rahim", "phone": "+880 1712-345678", "address": "House 5, Road 3, Mirpur, Dhaka",
            "area": "dhaka", "items": [{"product_id": self.shirt.id, "quantity": 2}],
        }
        args.update(over)
        return tools.place_order(self.conv, args)

    def test_search_finds_by_bangla_keyword_and_ranks_name_first(self):
        res = tools.search_products(self.conv, {"query": "polo shirt"})
        self.assertEqual(res["products"][0]["id"], self.shirt.id)
        res = tools.search_products(self.conv, {"query": "শার্ট"})
        self.assertEqual(res["products"][0]["id"], self.shirt.id)
        self.assertEqual(tools.search_products(self.conv, {"query": "zzzz"})["products"], [])

    def test_order_totals_phone_and_stock(self):
        res = self.order()
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["subtotal"], res["delivery_charge"], res["total"]), (1300, 80, 1380))
        o = Order.objects.get()
        self.assertEqual(o.phone, "01712345678")
        self.shirt.refresh_from_db()
        self.assertEqual(self.shirt.stock, 3)

    def test_outside_dhaka_charge(self):
        self.assertEqual(self.order(area="outside_dhaka")["total"], 1430)

    def test_validation_errors(self):
        self.assertFalse(self.order(phone="12345")["ok"])
        self.assertFalse(self.order(address="Dhaka")["ok"])
        self.assertFalse(self.order(area="mars")["ok"])
        self.assertFalse(self.order(items=[])["ok"])
        self.assertFalse(self.order(items=[{"product_id": 9999, "quantity": 1}])["ok"])
        self.assertFalse(self.order(items=[{"product_id": self.speaker.id, "quantity": 2}])["ok"])  # only 1 in stock
        self.assertEqual(Order.objects.count(), 0)

    def test_duplicate_within_two_minutes_returns_same_order(self):
        first, second = self.order(), self.order()
        self.assertEqual(first["order_id"], second["order_id"])
        self.assertEqual(Order.objects.count(), 1)
        self.shirt.refresh_from_db()
        self.assertEqual(self.shirt.stock, 3)  # decremented once

    def test_cancel_returns_stock_once(self):
        self.order()
        o = Order.objects.get()
        o.status = Order.Status.CANCELLED
        o.save()
        o.save()
        self.shirt.refresh_from_db()
        self.assertEqual(self.shirt.stock, 5)

    def test_request_human(self):
        tools.request_human(self.conv, {"reason": "refund"})
        self.conv.refresh_from_db()
        self.assertTrue(self.conv.human_handoff)
        self.assertEqual(self.conv.handoff_reason, "refund")


class HandlerTests(TestCase):
    def setUp(self):
        handler._recent.clear()
        p1 = patch("bot.handler.messenger.send_text")
        p2 = patch("bot.handler.messenger.send_action")
        p3 = patch("bot.handler.messenger.get_profile_name", return_value="Karim")
        p4 = patch("bot.handler.gemini.generate_reply", return_value="reply!")
        self.send, _, _, self.gen = (p.start() for p in (p1, p2, p3, p4))
        for p in (p1, p2, p3, p4):
            self.addCleanup(p.stop)

    def test_replies_and_stores_name(self):
        handler.process_message("u1", "m1", "hello")
        self.send.assert_called_once_with("u1", "reply!")
        self.assertEqual(Conversation.objects.get().customer_name, "Karim")

    def test_duplicate_mid_replied_once(self):
        handler.process_message("u1", "m1", "hello")
        handler.process_message("u1", "m1", "hello")
        self.assertEqual(self.send.call_count, 1)

    def test_bot_silent_during_handoff_but_records_message(self):
        Conversation.objects.create(psid="u1", human_handoff=True, handoff_at=timezone.now())
        handler.process_message("u1", "m1", "where is my order")
        self.send.assert_not_called()
        self.assertEqual(Conversation.objects.get().last_user_message, "where is my order")

    @override_settings(HANDOFF_AUTO_RESUME_HOURS=6)
    def test_handoff_expires(self):
        Conversation.objects.create(psid="u1", human_handoff=True, handoff_at=timezone.now() - timedelta(hours=7))
        handler.process_message("u1", "m1", "hi again")
        self.send.assert_called_once()
        self.assertFalse(Conversation.objects.get().human_handoff)

    def test_gemini_failure_sends_fallback(self):
        self.gen.side_effect = RuntimeError("boom")
        handler.process_message("u1", "m1", "hi")
        self.send.assert_called_once_with("u1", gemini.FALLBACK_REPLY)

    def test_attachment_only(self):
        handler.process_message("u1", "m1", "", has_attachment=True)
        self.send.assert_called_once_with("u1", handler.ATTACHMENT_REPLY)
        self.gen.assert_not_called()

    def test_rate_limit(self):
        for i in range(handler.RATE_MAX_MESSAGES + 5):
            handler.process_message("spam", f"m{i}", "hi")
        self.assertEqual(self.send.call_count, handler.RATE_MAX_MESSAGES)

    def test_echo_and_receipts_ignored(self):
        handler._handle({"sender": {"id": "u1"}, "message": {"mid": "e", "text": "x", "is_echo": True}})
        handler._handle({"sender": {"id": "u1"}, "delivery": {"mids": ["a"]}})
        self.send.assert_not_called()


class GeminiFlowTests(TestCase):
    """Fake Gemini: first asks to search, then to place an order, then answers."""

    def test_tool_loop_places_order_and_saves_history(self):
        shirt, _ = make_products()
        conv = Conversation.objects.create(psid="u1")

        def call(name, **args):
            return SimpleNamespace(name=name, args=args)

        def resp(calls=None, text=None):
            return SimpleNamespace(
                function_calls=calls, text=text,
                candidates=[SimpleNamespace(content=MagicMock(name="content"))],
            )

        script = [
            resp([call("search_products", query="polo")]),
            resp([call("place_order", customer_name="Rahim", phone="01712345678",
                       address="House 5, Road 3, Mirpur, Dhaka", area="dhaka",
                       items=[{"product_id": shirt.id, "quantity": 1}])]),
            resp(text="Order done! Total ৳730"),
        ]
        fake = MagicMock()
        fake.models.generate_content.side_effect = script
        with patch("bot.gemini._client", return_value=fake):
            reply = gemini.generate_reply(conv, "polo shirt lagbe")

        self.assertEqual(reply, "Order done! Total ৳730")
        self.assertEqual(Order.objects.get().total, 730)
        conv.refresh_from_db()
        self.assertEqual([m["role"] for m in conv.history], ["user", "model"])
        # the model saw real tool output
        second_call_contents = fake.models.generate_content.call_args_list[1].kwargs["contents"]
        self.assertGreaterEqual(len(second_call_contents), 3)

    def test_empty_model_reply_falls_back(self):
        conv = Conversation.objects.create(psid="u1")
        fake = MagicMock()
        fake.models.generate_content.return_value = SimpleNamespace(function_calls=None, text="", candidates=[])
        with patch("bot.gemini._client", return_value=fake):
            self.assertEqual(gemini.generate_reply(conv, "hi"), gemini.FALLBACK_REPLY)

    def test_system_prompt_has_delivery_and_shop_info(self):
        prompt = gemini.build_system_prompt()
        self.assertIn("৳80", prompt)
        self.assertIn("৳130", prompt)
        self.assertIn("Cash on Delivery", prompt)


@override_settings(ADMIN_API_TOKEN=TOKEN)
class ApiTests(TestCase):
    def setUp(self):
        self.shirt, _ = make_products()
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}
        conv = Conversation.objects.create(psid="u1")
        tools.place_order(conv, {
            "customer_name": "Rahim", "phone": "01712345678", "address": "House 5, Road 3, Mirpur, Dhaka",
            "area": "dhaka", "items": [{"product_id": self.shirt.id, "quantity": 1}],
        })

    def test_requires_token(self):
        self.assertEqual(self.client.get("/api/orders/").status_code, 401)
        self.assertEqual(self.client.get("/api/orders/", HTTP_AUTHORIZATION="Bearer wrong").status_code, 401)
        self.assertEqual(self.client.get("/api/ping/", **self.auth).status_code, 200)

    @override_settings(ADMIN_API_TOKEN="short")
    def test_short_token_disables_api(self):
        self.assertEqual(self.client.get("/api/ping/", HTTP_AUTHORIZATION="Bearer short").status_code, 503)

    def test_orders_list_and_status_patch(self):
        data = self.client.get("/api/orders/", **self.auth).json()
        self.assertEqual(data["counts"], {"new": 1})
        oid = data["orders"][0]["id"]
        self.assertEqual(data["orders"][0]["total"], 730)
        r = self.client.patch(f"/api/orders/{oid}/", json.dumps({"status": "shipped"}), content_type="application/json", **self.auth)
        self.assertEqual(r.json()["status"], "shipped")
        r = self.client.patch(f"/api/orders/{oid}/", json.dumps({"status": "bogus"}), content_type="application/json", **self.auth)
        self.assertEqual(r.status_code, 400)

    def test_cancelled_order_cannot_reopen(self):
        oid = Order.objects.get().id
        self.client.patch(f"/api/orders/{oid}/", json.dumps({"status": "cancelled"}), content_type="application/json", **self.auth)
        r = self.client.patch(f"/api/orders/{oid}/", json.dumps({"status": "new"}), content_type="application/json", **self.auth)
        self.assertEqual(r.status_code, 400)

    def test_chat_resume(self):
        conv = Conversation.objects.get()
        tools.request_human(conv, {"reason": "refund"})
        data = self.client.get("/api/chats/?handoff=1", **self.auth).json()
        self.assertEqual(len(data["chats"]), 1)
        r = self.client.patch(f"/api/chats/{conv.id}/", json.dumps({"human_handoff": False}), content_type="application/json", **self.auth)
        self.assertFalse(r.json()["human_handoff"])

    def test_product_search_and_patch_validation(self):
        data = self.client.get("/api/products/?q=polo", **self.auth).json()
        self.assertEqual(data["total"], 1)
        url = f"/api/products/{self.shirt.id}/"
        ok = self.client.patch(url, json.dumps({"price": 700, "stock": 9, "is_active": False}), content_type="application/json", **self.auth)
        self.assertEqual((ok.json()["price"], ok.json()["stock"], ok.json()["is_active"]), (700, 9, False))
        bad = self.client.patch(url, json.dumps({"price": -5}), content_type="application/json", **self.auth)
        self.assertEqual(bad.status_code, 400)

    def test_cors_preflight(self):
        r = self.client.options("/api/orders/", HTTP_ORIGIN="https://panel.vercel.app",
                                HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
                                HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization")
        self.assertEqual(r.status_code, 200)
        self.assertIn("access-control-allow-origin", {k.lower() for k in r.headers})


class ImporterTests(TestCase):
    def test_import_create_update_and_errors(self):
        csv1 = "sku,name,price,stock,category\nA1,Alpha,\"৳1,200\",5,Cat\nA2,Beta,abc,1,Cat\n,Gamma,50,,\n".encode("utf-8-sig")
        res = import_products_csv(csv1)
        self.assertEqual((res.created, res.updated, len(res.errors)), (2, 0, 1))
        self.assertEqual(Product.objects.get(sku="A1").price, 1200)
        self.assertTrue(Product.objects.filter(sku="gamma").exists())  # sku made from name
        res = import_products_csv(b"sku,name,price\nA1,Alpha New,1300\n")
        self.assertEqual((res.created, res.updated), (0, 1))
        self.assertEqual(Product.objects.get(sku="A1").stock, 5)  # untouched: no stock column

    def test_missing_columns(self):
        self.assertTrue(import_products_csv(b"title,cost\nx,1\n").errors)
