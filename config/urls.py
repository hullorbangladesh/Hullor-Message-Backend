from django.contrib import admin
from django.urls import include, path

from bot.views import health, webhook

urlpatterns = [
    path("", health),
    path("health", health),
    path("health/", health),
    path("webhook", webhook),
    path("webhook/", webhook),
    path("admin/", admin.site.urls),
    path("api/", include("api.urls")),
]
