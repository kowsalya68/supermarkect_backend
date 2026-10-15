
from django.contrib import admin
from django.urls import path
from store import views

urlpatterns = [
    # Admin
    path("admin/", admin.site.urls),

    # Authentication
    path("", views.login, name="login"),
    path("logout/", views.logout_view, name="logout"),

    # Dashboard
    path("dashboard/", views.dashboard, name="dashboard"),
    path("api/dashboard/", views.dashboard_api, name="dashboard_api"),
    path("dashboard-chat/", views.dashboard_chat_api, name="dashboard_chat_api"),

    # Products
    path("products/", views.products, name="products"),
    path("products/add/", views.add_product, name="add_product"),
    path("products/<int:product_id>/edit/", views.edit_product, name="edit_product"),
    path("products/<int:product_id>/delete/", views.delete_product, name="delete_product"),

    # Sales
    path("sales/", views.sales, name="sales"),

    # Billing
    path("billing/", views.billing, name="billing"),
    path("billing/complete/", views.complete_bill, name="complete_bill"),

    # AI Prediction
    path("aiprediction/", views.aiprediction, name="aiprediction"),
    path("aiprediction/api/", views.aiprediction_api, name="aiprediction_api"),

    # Previous Year Reports
    path("previousyear/", views.previousyear, name="previousyear"),
    path("previousyear/export/", views.export_previousyear, name="export_previousyear"),

    # Restock APIs
    path("api/restock/low-stock/", views.restock_low_stock_api, name="restock_low_stock_api"),
    path("api/restock/orders/", views.restock_orders_api, name="restock_orders_api"),
    path("api/restock/create/", views.restock_create_order, name="restock_create_order"),

    # Purchase Order Status
    path(
        "purchase-orders/<int:order_id>/status/",
        views.restock_update_status,
        name="restock_update_status",
    ),

    # Add Received Stock
    path(
        "purchase-orders/<int:order_id>/add-stock/",
        views.add_received_stock,
        name="restock_add_stock",
    ),
]
