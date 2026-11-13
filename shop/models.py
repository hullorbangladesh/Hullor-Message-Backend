from datetime import timedelta

from django.conf import settings
from django.db import models, transaction
from django.db.models import F
from django.utils import timezone


class Product(models.Model):
    sku = models.CharField(max_length=80, unique=True, null=True, blank=True)
    name = models.CharField(max_length=200)
    name_bn = models.CharField("Bangla name", max_length=200, blank=True)
    category = models.CharField(max_length=100, blank=True, db_index=True)
    keywords = models.TextField(
        blank=True,
        help_text="Extra search words (Bangla, Banglish, synonyms), separated by space or comma.",
    )
    description = models.TextField(blank=True)
    price = models.PositiveIntegerField(help_text="Price in BDT (taka)")
    stock = models.PositiveIntegerField(default=10)
    image_url = models.URLField(max_length=500, blank=True, help_text="Public https image link (optional)")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Conversation(models.Model):
    psid = models.CharField("Messenger user ID", max_length=64, unique=True)
    customer_name = models.CharField(max_length=120, blank=True)
    history = models.JSONField(default=list, blank=True)
    last_user_message = models.CharField(max_length=500, blank=True)
    last_message_at = models.DateTimeField(default=timezone.now, db_index=True)
    human_handoff = models.BooleanField(default=False)
    handoff_reason = models.CharField(max_length=300, blank=True)
    handoff_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-last_message_at"]

    def __str__(self):
        return self.customer_name or self.psid

    def handoff_active(self):
        """True while the bot should stay silent and a human is expected to reply."""
        if not self.human_handoff:
            return False
        hours = settings.HANDOFF_AUTO_RESUME_HOURS
        if hours > 0 and self.handoff_at and timezone.now() - self.handoff_at > timedelta(hours=hours):
            return False
        return True


class ProcessedMessage(models.Model):
    """Meta can deliver the same message twice; we remember message IDs to reply only once."""

    mid = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class Order(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "New"
        CONFIRMED = "confirmed", "Confirmed"
        SHIPPED = "shipped", "Shipped"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"

    class Area(models.TextChoices):
        DHAKA = "dhaka", "Inside Dhaka"
        OUTSIDE = "outside_dhaka", "Outside Dhaka"

    psid = models.CharField(max_length=64, blank=True, db_index=True)
    customer_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20)
    address = models.TextField()
    area = models.CharField(max_length=20, choices=Area.choices)
    subtotal = models.PositiveIntegerField()
    delivery_charge = models.PositiveIntegerField()
    total = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW, db_index=True)
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order #{self.pk} - {self.customer_name}"

    @classmethod
    def from_db(cls, db, field_names, values):
        obj = super().from_db(db, field_names, values)
        if "status" in field_names:
            obj._orig_status = obj.status
        return obj

    def save(self, *args, **kwargs):
        # When an order becomes "cancelled", put the items back into stock (once).
        cancelling = (
            self.pk
            and getattr(self, "_orig_status", None) not in (None, self.Status.CANCELLED)
            and self.status == self.Status.CANCELLED
        )
        with transaction.atomic():
            super().save(*args, **kwargs)
            if cancelling:
                for item in self.items.all():
                    if item.product_id:
                        Product.objects.filter(pk=item.product_id).update(stock=F("stock") + item.quantity)
        self._orig_status = self.status


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.SET_NULL)
    product_name = models.CharField(max_length=200)
    unit_price = models.PositiveIntegerField()
    quantity = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.quantity} x {self.product_name}"
