from django.contrib import admin
from django.urls import path
from store import views

urlpatterns = [

    path('admin/', admin.site.urls),

    # LOGIN
    path('', views.login, name='login'),

    # LOGOUT
    path('logout/', views.logout_view, name='logout'),

    # DASHBOARD UI
    path('dashboard/', views.dashboard, name='dashboard'),

    # DASHBOARD API
    path('api/dashboard/', views.dashboard_api, name='dashboard_api'),

   
    path("products/", views.products, name="products"),
    path("products/add/", views.add_product, name="add_product"),
    path("products/<int:product_id>/edit/", views.edit_product, name="edit_product"),
    path("products/<int:product_id>/delete/", views.delete_product, name="delete_product"),

    # SALES
    path('sales/', views.sales, name='sales'),

    # BILLING
 path('billing/', views.billing, name='billing'),
    path('billing/complete/',views.complete_bill,name='complete_bill'),

    # AI PREDICTION
       path(
        "aiprediction/",
        views.aiprediction,
        name="aiprediction"
    ),

    path(
        "aiprediction/api/",
        views.aiprediction_api,
        name="aiprediction_api"
    ),

  
path(
    "previousyear/",
    views.previousyear,
    name="previousyear"
),

path(
    "previousyear/export/",
    views.export_previousyear,
    name="export_previousyear"
),
path(
        "dashboard-chat/",
        views.dashboard_chat_api,
        name="dashboard_chat_api"
    ),

]


