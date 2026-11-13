from django.urls import path

from . import views

urlpatterns = [
    path("ping/", views.ping),
    path("orders/", views.orders_list),
    path("orders/<int:pk>/", views.order_detail),
    path("chats/", views.chats_list),
    path("chats/<int:pk>/", views.chat_detail),
    path("products/", views.products_list),
    path("products/<int:pk>/", views.product_detail),
]
