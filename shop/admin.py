from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import path

from .importer import import_products_csv
from .models import Conversation, Order, OrderItem, Product

admin.site.site_header = "Shop admin"
admin.site.site_title = "Shop admin"


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "price", "stock", "is_active")
    list_editable = ("price", "stock", "is_active")
    list_filter = ("is_active", "category")
    search_fields = ("name", "name_bn", "keywords", "sku", "category")
    list_per_page = 100
    change_list_template = "admin/shop/product/change_list.html"

    def get_urls(self):
        custom = [
            path(
                "import-csv/",
                self.admin_site.admin_view(self.import_csv_view),
                name="shop_product_import_csv",
            )
        ]
        return custom + super().get_urls()

    def import_csv_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied
        if request.method == "POST":
            upload = request.FILES.get("csv_file")
            if not upload:
                messages.error(request, "Please choose a CSV file.")
            else:
                result = import_products_csv(upload.read())
                if result.created or result.updated:
                    messages.success(request, f"Done: {result.created} created, {result.updated} updated.")
                for err in result.errors[:20]:
                    messages.warning(request, err)
                if len(result.errors) > 20:
                    messages.warning(request, f"...and {len(result.errors) - 20} more problems.")
                return redirect("admin:shop_product_changelist")
        context = dict(
            self.admin_site.each_context(request),
            title="Import products from CSV",
            opts=self.model._meta,
        )
        return render(request, "admin/shop/product/import_csv.html", context)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    raw_id_fields = ("product",)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "customer_name", "phone", "area", "total", "status", "created_at")
    list_filter = ("status", "area")
    search_fields = ("customer_name", "phone", "address")
    date_hierarchy = "created_at"
    inlines = [OrderItemInline]


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("__str__", "psid", "human_handoff", "last_user_message", "last_message_at")
    list_filter = ("human_handoff",)
    search_fields = ("customer_name", "psid", "last_user_message")
