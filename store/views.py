import calendar
import csv
import json
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING

import re


from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import (
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    Q,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, TruncMonth
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.core.paginator import Paginator

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import (
    Bill,
    BillItem,
    PredictionHistory,
    Product,
    RestockOrder,
    Sale,
)


# ==========================================================
# SHARED HELPERS
# ==========================================================

ZERO = Decimal("0.00")
MONEY_FIELD = DecimalField(max_digits=16, decimal_places=2)


def json_error(message, status=400):
    return JsonResponse({"success": False, "message": message}, status=status)


def parse_json_body(request):
    try:
        data = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("Invalid JSON request.")
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object.")
    return data


def decimal_value(value, field_name="amount"):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"Invalid {field_name}.")
    if not result.is_finite():
        raise ValueError(f"Invalid {field_name}.")
    return result


def admin_required(view_func):
    """Require an authenticated administrator for management pages/APIs."""
    return login_required(login_url="login")(view_func)


# ==========================================================
# LOGIN / LOGOUT
# ==========================================================

def login(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)

        if user and user.is_active and user.is_superuser:
            auth_login(request, user)
            return redirect("dashboard")

        if user:
            messages.error(request, "Only administrator accounts can login.")
        else:
            messages.error(request, "Invalid username or password.")

    return render(request, "login.html")


def logout_view(request):
    logout(request)
    messages.success(request, "You have logged out successfully.")
    return redirect("login")


# ==========================================================
# DASHBOARD
# ==========================================================

@admin_required
def dashboard(request):
    return render(request, "dashboard.html")


@admin_required
@require_GET
def dashboard_api(request):
    today = timezone.localdate()

    total_products = Product.objects.count()
    total_categories = Product.objects.values("category").distinct().count()
    low_stock = Product.objects.filter(
        stock__gt=0, stock__lte=F("min_stock")
    ).count()
    out_of_stock = Product.objects.filter(stock=0).count()

    total_sales = Sale.objects.aggregate(total=Sum("total_amount"))["total"] or ZERO
    current_month_sales = Sale.objects.filter(
        sale_date__year=today.year, sale_date__month=today.month
    ).aggregate(total=Sum("total_amount"))["total"] or ZERO

    first_day = today.replace(day=1)
    previous_month_end = first_day - timedelta(days=1)
    previous_month_sales = Sale.objects.filter(
        sale_date__year=previous_month_end.year,
        sale_date__month=previous_month_end.month,
    ).aggregate(total=Sum("total_amount"))["total"] or ZERO

    if previous_month_sales:
        sales_growth = (float(current_month_sales - previous_month_sales)
                        / float(previous_month_sales)) * 100
    else:
        sales_growth = 100.0 if current_month_sales else 0.0

    monthly_sales_data = []
    for offset in range(5, -1, -1):
        month_index = today.year * 12 + today.month - 1 - offset
        year, month = month_index // 12, month_index % 12 + 1
        total = Sale.objects.filter(
            sale_date__year=year, sale_date__month=month
        ).aggregate(total=Sum("total_amount"))["total"] or ZERO
        monthly_sales_data.append({
            "month": f"{calendar.month_abbr[month]} {year}",
            "sales": float(total),
        })

    top_products = Product.objects.annotate(
        total_sales=Sum("sales__total_amount"),
        monthly_product_sales=Sum(
            "sales__total_amount",
            filter=Q(
                sales__sale_date__year=today.year,
                sales__sale_date__month=today.month,
            ),
        ),
    ).order_by("-total_sales", "name")[:5]

    product_data = [{
        "id": p.id,
        "name": p.name,
        "sku": p.sku,
        "category": p.category,
        "price": float(p.price or 0),
        "cost_price": float(p.cost_price or 0),
        "stock": p.stock,
        "min_stock": p.min_stock,
        "stock_status": p.stock_status,
        "sales": float(p.monthly_product_sales or 0),
        "profit_per_unit": float(p.profit_per_unit or 0),
    } for p in top_products]

    prediction = PredictionHistory.objects.order_by("-created_at").first()
    prediction_data = None
    if prediction:
        prediction_data = {
            "month": str(prediction.prediction_month),
            "previous_sales": float(prediction.previous_sales or 0),
            "predicted_sales": float(prediction.predicted_sales or 0),
            "predicted_cost": float(prediction.predicted_cost or 0),
            "predicted_profit": float(prediction.predicted_profit or 0),
            "growth": float(prediction.predicted_growth or 0),
            "confidence": float(prediction.confidence or 0),
            "risk_level": prediction.risk_level,
            "status": prediction.status,
            "profit_margin": float(prediction.profit_margin or 0),
        }

    return JsonResponse({
        "success": True,
        "summary": {
            "total_products": total_products,
            "total_categories": total_categories,
            "total_sales": float(total_sales),
            "current_month_sales": float(current_month_sales),
            "previous_month_sales": float(previous_month_sales),
            "sales_growth": round(sales_growth, 2),
            "low_stock": low_stock,
            "out_of_stock": out_of_stock,
        },
        "monthly_sales": monthly_sales_data,
        "products": product_data,
        "prediction": prediction_data,
    })


# ==========================================================
# PRODUCT MANAGEMENT
# ==========================================================

@admin_required
def products(request):
    product_list = Product.objects.all().order_by("-created_at")
    search_query = request.GET.get("search", "").strip()
    selected_category = request.GET.get("category", "").strip()

    if search_query:
        product_list = product_list.filter(
            Q(name__icontains=search_query) | Q(sku__icontains=search_query)
        )

    valid_categories = [value for value, label in Product.CATEGORY_CHOICES]
    if selected_category in valid_categories:
        product_list = product_list.filter(category=selected_category)
    else:
        selected_category = ""

    all_products = Product.objects.all()
    paginator = Paginator(product_list, 10)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "products.html", {
        "products": page_obj.object_list,
        "page_obj": page_obj,
        "paginator": paginator,
        "categories": valid_categories,
        "selected_category": selected_category,
        "search_query": search_query,
        "total_products": all_products.count(),
        "in_stock": all_products.filter(stock__gt=F("min_stock")).count(),
        "low_stock": all_products.filter(stock__gt=0, stock__lte=F("min_stock")).count(),
        "out_of_stock": all_products.filter(stock=0).count(),
    })


def product_payload(product):
    return {
        "id": product.id,
        "name": product.name,
        "sku": product.sku,
        "category": product.category,
        "price": str(product.price),
        "cost_price": str(product.cost_price),
        "stock": product.stock,
        "min_stock": product.min_stock,
        "supplier": product.supplier,
        "status": product.stock_status,
    }


@admin_required
@require_POST
def add_product(request):
    try:
        name = request.POST.get("name", "").strip()
        sku = request.POST.get("sku", "").strip()
        category = request.POST.get("category", "").strip()
        if not name or not sku:
            return json_error("Product name and SKU are required.")
        if Product.objects.filter(sku=sku).exists():
            return json_error("SKU already exists.")
        if category not in dict(Product.CATEGORY_CHOICES):
            return json_error("Select a valid category.")

        product = Product.objects.create(
            name=name,
            sku=sku,
            category=category,
            price=decimal_value(request.POST.get("price"), "price"),
            cost_price=decimal_value(request.POST.get("cost_price", "0"), "cost price"),
            stock=int(request.POST.get("stock", 0)),
            min_stock=int(request.POST.get("min_stock", 10)),
            supplier=request.POST.get("supplier", "").strip(),
        )
        return JsonResponse({
            "success": True,
            "message": "Product added successfully.",
            "product": product_payload(product),
        })
    except (ValueError, TypeError, InvalidOperation) as exc:
        return json_error(str(exc))
    except Exception:
        return json_error("Could not add product. Check the entered values.")


@admin_required
@require_POST
def edit_product(request, product_id):
    product = get_object_or_404(Product, pk=product_id)
    try:
        name = request.POST.get("name", product.name).strip()
        sku = request.POST.get("sku", product.sku).strip()
        category = request.POST.get("category", product.category).strip()
        if not name or not sku:
            return json_error("Product name and SKU are required.")
        if Product.objects.filter(sku=sku).exclude(pk=product.pk).exists():
            return json_error("SKU already exists.")
        if category not in dict(Product.CATEGORY_CHOICES):
            return json_error("Select a valid category.")

        product.name = name
        product.sku = sku
        product.category = category
        product.price = decimal_value(request.POST.get("price", product.price), "price")
        product.cost_price = decimal_value(
            request.POST.get("cost_price", product.cost_price), "cost price"
        )
        product.stock = int(request.POST.get("stock", product.stock))
        product.min_stock = int(request.POST.get("min_stock", product.min_stock))
        product.supplier = request.POST.get("supplier", product.supplier).strip()
        if product.stock < 0 or product.min_stock < 0:
            return json_error("Stock values cannot be negative.")
        product.save()
        return JsonResponse({
            "success": True,
            "message": "Product updated successfully.",
            "product": product_payload(product),
        })
    except (ValueError, TypeError, InvalidOperation) as exc:
        return json_error(str(exc))
    except Exception:
        return json_error("Could not update product. Check the entered values.")


@admin_required
@require_POST
def delete_product(request, product_id):
    product = get_object_or_404(Product, pk=product_id)
    try:
        name = product.name
        product.delete()
        return JsonResponse({"success": True, "message": f"{name} deleted successfully."})
    except Exception:
        return json_error(
            "This product cannot be deleted because it is referenced by existing records."
        )


# ==========================================================
# SALES MANAGEMENT
# ==========================================================

# ==========================================================
# SALES MANAGEMENT
# ==========================================================
@admin_required
def sales(request):
    today = timezone.localdate()

    # Get all sales
    all_sales = Sale.objects.select_related("product").all()
    sales_list = all_sales.order_by("-sale_date", "-id")

    # Read filters
    search = request.GET.get("search", "").strip()
    date_filter = request.GET.get("date", "all")
    payment_filter = request.GET.get("payment", "all")

    # Search by product, SKU, customer mobile, or sale ID
    if search:
        query = (
            Q(product__name__icontains=search)
            | Q(product__sku__icontains=search)
            | Q(customer_mobile__icontains=search)
        )

        if search.isdigit():
            query |= Q(id=int(search))

        sales_list = sales_list.filter(query)

    # Date filters
    month_start = today.replace(day=1)

    if date_filter == "today":
        sales_list = sales_list.filter(sale_date__date=today)

    elif date_filter == "week":
        week_start = today - timedelta(days=today.weekday())
        sales_list = sales_list.filter(
            sale_date__date__gte=week_start,
            sale_date__date__lte=today,
        )

    elif date_filter == "month":
        sales_list = sales_list.filter(
            sale_date__date__gte=month_start,
            sale_date__date__lte=today,
        )

    elif date_filter == "last_month":
        last_end = month_start - timedelta(days=1)
        last_start = last_end.replace(day=1)

        sales_list = sales_list.filter(
            sale_date__date__gte=last_start,
            sale_date__date__lte=last_end,
        )

    else:
        date_filter = "all"

    # Payment filters
    if payment_filter in ("Cash", "UPI", "Card"):
        sales_list = sales_list.filter(
            payment_method=payment_filter
        )
    else:
        payment_filter = "all"

    # Pagination
    paginator = Paginator(sales_list, 10)
    page_obj = paginator.get_page(request.GET.get("page"))

    # Overall sales statistics
    total_sales = (
        all_sales.aggregate(total=Sum("total_amount"))["total"]
        or ZERO
    )

    transaction_count = all_sales.count()

    today_sales = (
        all_sales.filter(
            sale_date__date=today
        ).aggregate(total=Sum("total_amount"))["total"]
        or ZERO
    )

    monthly_sales = (
        all_sales.filter(
            sale_date__date__gte=month_start,
            sale_date__date__lte=today,
        ).aggregate(total=Sum("total_amount"))["total"]
        or ZERO
    )

    # Previous month sales
    last_end = month_start - timedelta(days=1)
    last_start = last_end.replace(day=1)

    previous_month_sales = (
        all_sales.filter(
            sale_date__date__gte=last_start,
            sale_date__date__lte=last_end,
        ).aggregate(total=Sum("total_amount"))["total"]
        or ZERO
    )

    # Monthly growth
    if previous_month_sales:
        growth = (
            float(monthly_sales - previous_month_sales)
            / float(previous_month_sales)
        ) * 100
    else:
        growth = 100.0 if monthly_sales else 0.0

    # Monthly chart for the current year
    monthly_chart = (
        all_sales.filter(sale_date__year=today.year)
        .annotate(month=TruncMonth("sale_date"))
        .values("month")
        .annotate(total=Sum("total_amount"))
        .order_by("month")
    )

    totals_by_month = {
        row["month"].month: float(row["total"] or 0)
        for row in monthly_chart
    }

    # Render template
    return render(
        request,
        "sales.html",
        {
            "sales": page_obj,
            "page_obj": page_obj,
            "search": search,
            "date_filter": date_filter,
            "payment_filter": payment_filter,
            "total_sales": total_sales,
            "today_sales": today_sales,
            "monthly_sales": monthly_sales,
            "previous_month_sales": previous_month_sales,
            "sales_growth": round(growth, 2),
            "transaction_count": transaction_count,
            "average_sale": (
                float(total_sales) / transaction_count
                if transaction_count
                else 0
            ),
            "chart_labels": [
                calendar.month_abbr[m]
                for m in range(1, 13)
            ],
            "chart_values": [
                totals_by_month.get(m, 0)
                for m in range(1, 13)
            ],
        },
    )


# ==========================================================
# BILLING PAGE
# ==========================================================

@admin_required
def billing(request):
    products = Product.objects.filter(
        stock__gt=0
    ).order_by("name")

    return render(
        request,
        "billing.html",
        {
            "products": products,
        },
    )


# ==========================================================
# COMPLETE BILL
# ==========================================================
# ==========================================================
# COMPLETE BILL
# ==========================================================

@admin_required
@require_POST
def complete_bill(request):
    try:
        # 1. Parse the JSON request
        data = parse_json_body(request)

        # 2. Validate customer mobile number
        customer_mobile = str(
            data.get("customer_mobile", "")
        ).strip()

        if not customer_mobile:
            return json_error(
                "Please enter the customer mobile number."
            )

        if not re.fullmatch(r"[0-9]{10}", customer_mobile):
            return json_error(
                "Enter a valid 10-digit mobile number."
            )

        # 3. Validate bill items
        items = data.get("items", [])

        if not isinstance(items, list) or not items:
            return json_error(
                "Please add at least one product."
            )

        # 4. Validate payment method
        payment_method = str(
            data.get("payment_method", "Cash")
        ).strip()

        if payment_method not in ("Cash", "UPI", "Card"):
            return json_error("Invalid payment method.")

        # 5. Validate discount and received amount
        discount = decimal_value(
            data.get("discount", "0"),
            "discount"
        )

        amount_received = decimal_value(
            data.get("amount_received", "0"),
            "amount received"
        )

        if discount < 0 or amount_received < 0:
            return json_error(
                "Discount and amount received cannot be negative."
            )

        # 6. Validate product IDs and quantities
        quantities = {}

        for item in items:
            if not isinstance(item, dict):
                return json_error(
                    "Invalid product information."
                )

            try:
                product_id = int(item["product_id"])
                quantity = int(item["quantity"])
            except (KeyError, TypeError, ValueError):
                return json_error(
                    "Product ID and quantity are required."
                )

            # Reject values such as 1.5 or booleans
            if (
                isinstance(item["product_id"], bool)
                or str(item["product_id"]).strip() != str(product_id)
            ):
                return json_error("Invalid product ID.")

            if (
                isinstance(item["quantity"], bool)
                or str(item["quantity"]).strip() != str(quantity)
            ):
                return json_error("Quantity must be a whole number.")

            if product_id <= 0:
                return json_error("Invalid product ID.")

            if quantity <= 0:
                return json_error(
                    "Quantity must be greater than zero."
                )

            quantities[product_id] = (
                quantities.get(product_id, 0) + quantity
            )

        # 7. Process bill atomically
        with transaction.atomic():

            # Lock products to prevent simultaneous stock overselling
            locked_products = (
                Product.objects.select_for_update()
                .filter(pk__in=quantities.keys())
                .order_by("pk")
            )

            product_map = {
                product.pk: product
                for product in locked_products
            }

            if len(product_map) != len(quantities):
                return json_error(
                    "One or more selected products no longer exist."
                )

            # 8. Calculate subtotal and check stock
            subtotal = Decimal("0.00")

            for product_id, quantity in quantities.items():
                product = product_map[product_id]

                if product.stock < quantity:
                    return json_error(
                        f"Insufficient stock for {product.name}. "
                        f"Available stock: {product.stock}."
                    )

                subtotal += product.price * quantity

            subtotal = subtotal.quantize(Decimal("0.01"))

            # 9. Calculate discount, tax, and total
            if discount > subtotal:
                return json_error(
                    "Discount cannot exceed the subtotal."
                )

            discount = discount.quantize(Decimal("0.01"))

            taxable_amount = subtotal - discount

            # Keep this tax rate consistent with your billing UI.
            tax = (
                taxable_amount * Decimal("0.05")
            ).quantize(Decimal("0.01"))

            total = (taxable_amount + tax).quantize(
                Decimal("0.01")
            )

            amount_received = amount_received.quantize(
                Decimal("0.01")
            )

            if amount_received < total:
                return json_error(
                    f"Amount received must be at least ₹{total}."
                )

            change_amount = (
                amount_received - total
            ).quantize(Decimal("0.01"))

            # 10. Create the bill
            bill = Bill.objects.create(
                bill_number=(
                    f"BILL-{uuid.uuid4().hex[:12].upper()}"
                ),
                customer_mobile=customer_mobile,
                subtotal=subtotal,
                discount=discount,
                tax=tax,
                total=total,
                payment_method=payment_method,
                amount_received=amount_received,
                change_amount=change_amount,
            )

            # 11. Create bill items, sales records, and update stock
            for product_id, quantity in quantities.items():
                product = product_map[product_id]

                line_total = (
                    product.price * quantity
                ).quantize(Decimal("0.01"))

                BillItem.objects.create(
                    bill=bill,
                    product=product,
                    quantity=quantity,
                    price=product.price,
                    total=line_total,
                )

                # Reduce inventory
                product.stock -= quantity
                product.save(
                    update_fields=["stock", "updated_at"]
                )

                # Record the sale
                Sale.objects.create(
                    product=product,
                    quantity=quantity,
                    selling_price=product.price,
                    total_amount=line_total,
                    payment_method=payment_method,
                    customer_mobile=customer_mobile,
                )

        # 12. Return success response to the frontend
        return JsonResponse({
            "success": True,
            "message": "Bill completed successfully.",
            "bill_number": bill.bill_number,
            "date": timezone.localtime(
                bill.created_at
            ).strftime("%d %b %Y, %I:%M %p"),
            "customer_mobile": customer_mobile,
            "subtotal": str(bill.subtotal),
            "discount": str(bill.discount),
            "tax": str(bill.tax),
            "total": str(bill.total),
            "payment_method": bill.payment_method,
            "amount_received": str(bill.amount_received),
            "change_amount": str(bill.change_amount),
        })

    except (ValueError, TypeError, KeyError, InvalidOperation):
        return json_error(
            "Invalid billing information. Check your entries."
        )

    except Exception:
        # Print the actual error in the Django terminal
        import logging
        logging.getLogger(__name__).exception(
            "Failed to complete supermarket bill"
        )

        return json_error(
            "Unable to complete the bill. Check the server terminal."
        )
# ==========================================================
# NEXT-MONTH BUSINESS PREDICTION
# ==========================================================


import calendar
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import Product, Sale


def get_business_prediction():
    today = timezone.localdate()

    # -----------------------------------------
    # 1. Identify the next forecast month
    # -----------------------------------------
    if today.month == 12:
        forecast_year = today.year + 1
        forecast_month = 1
    else:
        forecast_year = today.year
        forecast_month = today.month + 1

    forecast_days = calendar.monthrange(
        forecast_year, forecast_month
    )[1]

    forecast_start = today.replace(
        day=1
    )

    if forecast_start.month == 12:
        forecast_start = forecast_start.replace(
            year=forecast_start.year + 1, month=1
        )
    else:
        forecast_start = forecast_start.replace(
            month=forecast_start.month + 1
        )

    forecast_end = forecast_start.replace(
        day=forecast_days
    )

    # -----------------------------------------
    # 2. Load ALL historical sales
    #    Do not limit the main dataset to 90 days
    # -----------------------------------------
    all_sales = Sale.objects.filter(
        sale_date__lt=forecast_start
    ).select_related("product").order_by("sale_date")

    # Monthly revenue and cost
    monthly_data = defaultdict(
        lambda: {
            "revenue": Decimal("0"),
            "cost": Decimal("0"),
            "units": 0,
        }
    )

    # Recent sales are used only as an additional trend
    recent_start = today - timedelta(days=89)

    recent_revenue = Decimal("0")
    recent_cost = Decimal("0")
    recent_units = defaultdict(int)

    for sale in all_sales:
        sale_day = sale.sale_date.date() if hasattr(
            sale.sale_date, "date"
        ) else sale.sale_date

        year_month = (sale_day.year, sale_day.month)

        quantity = sale.quantity or 0
        revenue = Decimal(str(
            sale.total_amount
            if sale.total_amount is not None
            else (sale.selling_price or 0) * quantity
        ))

        product_cost = Decimal(str(
            sale.product.cost_price or 0
        )) * quantity

        monthly_data[year_month]["revenue"] += revenue
        monthly_data[year_month]["cost"] += product_cost
        monthly_data[year_month]["units"] += quantity

        if recent_start <= sale_day <= today:
            recent_revenue += revenue
            recent_cost += product_cost
            recent_units[sale.product_id] += quantity

    # -----------------------------------------
    # 3. Previous-year seasonal forecast
    #    Use the same calendar month in every
    #    available previous year
    # -----------------------------------------
    seasonal_revenues = []
    seasonal_costs = []

    for (year, month), values in monthly_data.items():
        if month == forecast_month and year < forecast_year:
            seasonal_revenues.append(values["revenue"])
            seasonal_costs.append(values["cost"])

    if seasonal_revenues:
        seasonal_sales = sum(
            seasonal_revenues, Decimal("0")
        ) / Decimal(len(seasonal_revenues))

        seasonal_cost = sum(
            seasonal_costs, Decimal("0")
        ) / Decimal(len(seasonal_costs))
    else:
        seasonal_sales = None
        seasonal_cost = None

    # -----------------------------------------
    # 4. Recent daily trend projected into
    #    the forecast month
    # -----------------------------------------
    recent_days = max(
        1,
        (today - recent_start).days + 1
    )

    recent_sales_forecast = (
        recent_revenue / Decimal(recent_days)
    ) * Decimal(forecast_days)

    recent_cost_forecast = (
        recent_cost / Decimal(recent_days)
    ) * Decimal(forecast_days)

    # -----------------------------------------
    # 5. Combine historical seasonal data and
    #    recent trends
    # -----------------------------------------
    if seasonal_sales is not None:
        # 60% previous matching months,
        # 40% recent sales trend
        predicted_sales = (
            seasonal_sales * Decimal("0.60")
            + recent_sales_forecast * Decimal("0.40")
        )

        predicted_cost = (
            seasonal_cost * Decimal("0.60")
            + recent_cost_forecast * Decimal("0.40")
        )

        forecast_method = (
            "Previous-year same-month history "
            "and recent 90-day trend"
        )

    elif all_sales.exists():
        # No matching historical month:
        # use recent trend if available;
        # otherwise use all-history averages
        if recent_revenue > 0:
            predicted_sales = recent_sales_forecast
            predicted_cost = recent_cost_forecast
            forecast_method = "Recent sales trend; no matching month history"
        else:
            total_revenue = sum(
                (v["revenue"] for v in monthly_data.values()),
                Decimal("0")
            )
            total_cost = sum(
                (v["cost"] for v in monthly_data.values()),
                Decimal("0")
            )

            first_sale = all_sales.order_by(
                "sale_date"
            ).first()

            first_day = first_sale.sale_date.date() if hasattr(
                first_sale.sale_date, "date"
            ) else first_sale.sale_date

            history_days = max(
                1, (today - first_day).days + 1
            )

            predicted_sales = (
                total_revenue / Decimal(history_days)
            ) * Decimal(forecast_days)

            predicted_cost = (
                total_cost / Decimal(history_days)
            ) * Decimal(forecast_days)

            forecast_method = "All-history daily average"

    else:
        predicted_sales = Decimal("0")
        predicted_cost = Decimal("0")
        forecast_method = "Insufficient historical sales data"

    # -----------------------------------------
    # 6. Calculate expected profit or loss
    # -----------------------------------------
    predicted_profit = predicted_sales - predicted_cost

    if predicted_sales > 0:
        profit_margin = (
            predicted_profit / predicted_sales
        ) * Decimal("100")
    else:
        profit_margin = Decimal("0")

    # -----------------------------------------
    # 7. Find high-demand products using
    #    recent sales and check current stock
    # -----------------------------------------
    products = Product.objects.all()

    high_demand_products = []
    loss_risk_products = []

    for product in products:
        units_sold = recent_units.get(product.id, 0)
        stock = product.stock or 0
        minimum_stock = product.min_stock or 0

        if units_sold > 0:
            daily_demand = Decimal(units_sold) / Decimal(recent_days)
            estimated_monthly_demand = int(
                daily_demand * Decimal(forecast_days)
            )

            if estimated_monthly_demand > 0 and stock < estimated_monthly_demand:
                high_demand_products.append({
                    "id": product.id,
                    "name": product.name,
                    "category": product.category,
                    "stock": stock,
                    "predicted_demand": estimated_monthly_demand,
                    "shortage": max(
                        0, estimated_monthly_demand - stock
                    ),
                })

        if stock <= minimum_stock:
            loss_risk_products.append({
                "id": product.id,
                "name": product.name,
                "category": product.category,
                "stock": stock,
                "min_stock": minimum_stock,
                "risk": "Low stock",
            })

    # Highest shortage first
    high_demand_products.sort(
        key=lambda item: item["shortage"],
        reverse=True
    )

    # -----------------------------------------
    # 8. Return values for your existing
    #    prediction page
    # -----------------------------------------
    return {
        "forecast_month": forecast_start.strftime("%B %Y"),
        "forecast_start": forecast_start,
        "forecast_end": forecast_end,
        "predicted_sales": round(predicted_sales, 2),
        "predicted_cost": round(predicted_cost, 2),
        "predicted_profit": round(predicted_profit, 2),
        "profit_margin": round(profit_margin, 2),
        "forecast_method": forecast_method,
        "high_demand_products": high_demand_products,
        "loss_risk_products": loss_risk_products,
        "is_profit": predicted_profit >= 0,
        "historical_months_used": len(seasonal_revenues),
    }




def make_json_safe(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: make_json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [make_json_safe(item) for item in value]
    return value



@admin_required
def aiprediction(request):
    data = get_business_prediction()

    return render(request, "aiprediction.html", {
        "forecast": data,
        "high_demand_products": data.get("high_demand_products", []),
        "loss_risk_products": data.get("loss_risk_products", []),
        "allocation_items": data.get("allocation_items", []),
    })


@admin_required
@require_GET
def aiprediction_api(request):
    return JsonResponse({
        "success": True,
        "forecast": make_json_safe(get_business_prediction()),
    })


# ==========================================================
# PREVIOUS-YEAR REPORT HELPERS AND PAGE
# ==========================================================

def get_yearly_report_data(year):
    zero = ZERO
    year_sales = Sale.objects.filter(sale_date__year=year)
    cost_expression = ExpressionWrapper(
        F("quantity") * F("product__cost_price"),
        output_field=MONEY_FIELD,
    )
    totals = year_sales.aggregate(
        revenue=Sum("total_amount"),
        cost=Sum(cost_expression),
    )
    revenue = totals["revenue"] or zero
    cost = totals["cost"] or zero
    profit = revenue - cost
    transaction_count = year_sales.count()
    margin = profit / revenue * 100 if revenue else zero

    monthly_rows = []
    for month in range(1, 13):
        qs = year_sales.filter(sale_date__month=month)
        row_totals = qs.aggregate(revenue=Sum("total_amount"), cost=Sum(cost_expression))
        month_revenue = row_totals["revenue"] or zero
        month_cost = row_totals["cost"] or zero
        month_profit = month_revenue - month_cost
        month_margin = month_profit / month_revenue * 100 if month_revenue else zero
        monthly_rows.append({
            "month_number": month,
            "month": calendar.month_name[month],
            "short_month": calendar.month_abbr[month],
            "sales": month_revenue,
            "cost": month_cost,
            "profit": month_profit,
            "margin": round(float(month_margin), 2),
            "transactions": qs.count(),
        })

    best_month = max(monthly_rows, key=lambda row: row["sales"], default=None)
    category_rows = year_sales.values("product__category").annotate(
        revenue=Sum("total_amount")
    ).order_by("-revenue")
    colors = ["#6478f5", "#20b486", "#f4a340", "#ec6b83",
              "#8b5cf6", "#17a2b8", "#e87932", "#64748b"]
    category_data = []
    for index, row in enumerate(category_rows):
        cat_revenue = row["revenue"] or zero
        category_data.append({
            "name": row["product__category"] or "Uncategorized",
            "revenue": cat_revenue,
            "percentage": round(float(cat_revenue / revenue * 100), 1) if revenue else 0,
            "color": colors[index % len(colors)],
        })

    top_products = list(
        year_sales.values("product_id", "product__name", "product__category")
        .annotate(revenue=Sum("total_amount"), units_sold=Sum("quantity"))
        .order_by("-revenue")[:5]
    )
    for rank, item in enumerate(top_products, start=1):
        item["rank"] = rank
        item["name"] = item["product__name"]
        item["category"] = item["product__category"] or "Uncategorized"
        item["revenue"] = item["revenue"] or zero

    if not revenue:
        performance = "No Sales Data"
    elif margin >= 20:
        performance = "Excellent"
    elif margin >= 10:
        performance = "Good"
    elif margin >= 0:
        performance = "Average"
    else:
        performance = "Loss"

    return {
        "year": year,
        "total_revenue": revenue,
        "total_cost": cost,
        "total_profit": profit,
        "average_monthly_sales": revenue / Decimal("12"),
        "profit_margin": round(float(margin), 2),
        "transaction_count": transaction_count,
        "best_month": best_month,
        "monthly_rows": monthly_rows,
        "category_data": category_data,
        "top_products": top_products,
        "performance": performance,
    }


@admin_required
def previousyear(request):
    current_year = timezone.localdate().year
    try:
        selected_year = int(request.GET.get("year", current_year - 1))
    except (TypeError, ValueError):
        selected_year = current_year - 1
    try:
        compare_year = int(request.GET.get("compare_year", selected_year - 1))
    except (TypeError, ValueError):
        compare_year = selected_year - 1

    selected_year = max(2000, min(selected_year, current_year))
    compare_year = max(2000, min(compare_year, current_year))
    report = get_yearly_report_data(selected_year)
    comparison = get_yearly_report_data(compare_year)

    if comparison["total_revenue"]:
        growth = (
            (report["total_revenue"] - comparison["total_revenue"])
            / comparison["total_revenue"] * 100
        )
    else:
        growth = Decimal("100") if report["total_revenue"] else ZERO

    return render(request, "previousyear.html", {
        "report": report,
        "comparison": comparison,
        "selected_year": selected_year,
        "compare_year": compare_year,
        "available_years": range(current_year, 1999, -1),
        "growth": round(float(growth), 2),
        "chart_labels": [row["short_month"] for row in report["monthly_rows"]],
        "chart_current": [float(row["sales"]) for row in report["monthly_rows"]],
        "chart_previous": [float(row["sales"]) for row in comparison["monthly_rows"]],
    })


# ==========================================================
# EXPORT PREVIOUS-YEAR REPORT AS EXCEL
# ==========================================================

@admin_required
@require_GET
def export_previousyear(request):
    current_year = timezone.localdate().year
    try:
        year = int(request.GET.get("year", current_year - 1))
        year = max(2000, min(year, current_year))
    except (TypeError, ValueError):
        year = current_year - 1

    sales_records = list(
        Sale.objects.filter(sale_date__year=year)
        .select_related("product")
        .order_by("sale_date")
    )
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Year Summary"
    summary.append(["Supermarket AI - Sales Report", year])
    summary.append(["Metric", "Value"])

    transactions = workbook.create_sheet("Transactions")
    transactions.append([
        "Sale Date", "Product", "Category", "Quantity", "Selling Price",
        "Unit Cost", "Revenue", "Estimated Cost", "Estimated Profit",
        "Payment Method", "Customer",
    ])

    monthly = {
        m: {"revenue": ZERO, "cost": ZERO, "transactions": 0}
        for m in range(1, 13)
    }
    categories = {}
    products_data = {}
    revenue_total = ZERO
    cost_total = ZERO
    units_total = 0

    for sale in sales_records:
        product = sale.product
        qty = sale.quantity or 0
        selling_price = Decimal(str(sale.selling_price or 0))
        revenue = Decimal(str(sale.total_amount or selling_price * qty))
        unit_cost = Decimal(str(product.cost_price or 0))
        cost = unit_cost * qty
        profit = revenue - cost
        category = product.category or "Uncategorized"

        revenue_total += revenue
        cost_total += cost
        units_total += qty
        monthly[sale.sale_date.month]["revenue"] += revenue
        monthly[sale.sale_date.month]["cost"] += cost
        monthly[sale.sale_date.month]["transactions"] += 1
        categories[category] = categories.get(category, ZERO) + revenue

        pdata = products_data.setdefault(product.pk, {
            "name": product.name, "category": category,
            "units": 0, "revenue": ZERO,
        })
        pdata["units"] += qty
        pdata["revenue"] += revenue

        transactions.append([
            str(sale.sale_date), product.name, category, qty,
            float(selling_price), float(unit_cost), float(revenue),
            float(cost), float(profit), sale.payment_method,
            sale.customer_name or "",
        ])

    profit_total = revenue_total - cost_total
    margin = float(profit_total / revenue_total * 100) if revenue_total else 0
    for label, value in [
        ("Total Revenue", float(revenue_total)),
        ("Estimated Cost", float(cost_total)),
        ("Estimated Gross Profit", float(profit_total)),
        ("Gross Profit Margin (%)", margin),
        ("Sales Records", len(sales_records)),
        ("Total Units Sold", units_total),
        ("Average Monthly Sales", float(revenue_total / 12)),
    ]:
        summary.append([label, value])

    monthly_sheet = workbook.create_sheet("Monthly Report")
    monthly_sheet.append([
        "Month", "Sales", "Estimated Cost", "Profit / Loss",
        "Profit Margin (%)", "Transactions",
    ])
    for month in range(1, 13):
        values = monthly[month]
        rev, cost = values["revenue"], values["cost"]
        profit = rev - cost
        month_margin = float(profit / rev * 100) if rev else 0
        monthly_sheet.append([
            calendar.month_name[month], float(rev), float(cost),
            float(profit), month_margin, values["transactions"],
        ])

    category_sheet = workbook.create_sheet("Categories")
    category_sheet.append(["Category", "Revenue", "Revenue Share (%)"])
    for name, value in sorted(categories.items(), key=lambda x: x[1], reverse=True):
        share = float(value / revenue_total * 100) if revenue_total else 0
        category_sheet.append([name, float(value), share])

    products_sheet = workbook.create_sheet("Products")
    products_sheet.append(["Rank", "Product Name", "Category", "Units Sold", "Revenue"])
    ranked = sorted(products_data.values(), key=lambda x: x["revenue"], reverse=True)
    for rank, item in enumerate(ranked, start=1):
        products_sheet.append([
            rank, item["name"], item["category"], item["units"], float(item["revenue"])
        ])

    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill(fill_type="solid", fgColor="334155")
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for column in sheet.columns:
            letter = get_column_letter(column[0].column)
            max_length = max(
                (len(str(cell.value)) for cell in column if cell.value is not None),
                default=10,
            )
            sheet.column_dimensions[letter].width = min(max(max_length + 3, 12), 35)
        if sheet.max_row >= 2:
            sheet.auto_filter.ref = sheet.dimensions

    summary["A1"].font = Font(bold=True, size=16, color="1D4ED8")
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = (
        f'attachment; filename="supermarket_report_{year}.xlsx"'
    )
    response["Cache-Control"] = "no-store"
    workbook.save(response)
    return response


# ==========================================================
# DASHBOARD CHATBOT (RULE-BASED; NO EXTERNAL AI API)
# ==========================================================

@admin_required
@require_POST
def dashboard_chat_api(request):
    try:
        data = parse_json_body(request)
        question = str(data.get("message", "")).strip().lower()
        if not question:
            return JsonResponse({"answer": "Please enter a question."}, status=400)

        if any(word in question for word in ("restock", "low stock", "stock")):
            products = Product.objects.filter(stock__lte=F("min_stock")).order_by("stock")
            if not products.exists():
                answer = "All products currently have sufficient stock."
            else:
                answer = "Products needing restocking:\n" + "\n".join(
                    f"• {p.name}: {p.stock} units available (minimum: {p.min_stock})."
                    for p in products[:10]
                )
        elif any(word in question for word in (
            "highest sales", "top products", "best selling", "most sold", "popular products"
        )):
            top = Sale.objects.values("product__name").annotate(
                total_quantity=Sum("quantity"), total_sales=Sum("total_amount")
            ).order_by("-total_sales")[:5]
            answer = (
                "No sales records are available yet."
                if not top else
                "Top-selling products by sales value:\n" + "\n".join(
                    f"{i}. {row['product__name']} — ₹{row['total_sales'] or 0} "
                    f"(quantity sold: {row['total_quantity'] or 0})."
                    for i, row in enumerate(top, start=1)
                )
            )
        elif "profit" in question or "loss" in question:
            cost_expr = ExpressionWrapper(
                F("quantity") * F("product__cost_price"),
                output_field=MONEY_FIELD,
            )
            totals = Sale.objects.aggregate(
                revenue=Sum("total_amount"), cost=Sum(cost_expr)
            )
            revenue = totals["revenue"] or ZERO
            cost = totals["cost"] or ZERO
            profit = revenue - cost
            if profit > 0:
                answer = (
                    f"Recorded estimated gross profit is ₹{profit:,.2f}. "
                    "This excludes operating expenses such as rent and wages."
                )
            elif profit < 0:
                answer = (
                    f"Recorded estimated gross loss is ₹{abs(profit):,.2f}. "
                    "This excludes operating expenses."
                )
            else:
                answer = "Recorded sales and product cost are equal, or there are no sales."
        elif "sales" in question or "revenue" in question:
            total = Sale.objects.aggregate(total=Sum("total_amount"))["total"] or ZERO
            answer = f"Total recorded sales are ₹{total:,.2f}."
        elif "product" in question:
            answer = f"Your supermarket currently has {Product.objects.count()} products."
        else:
            answer = (
                "I can answer questions about your records. Try asking:\n"
                "• Which products need restocking?\n"
                "• Which products have the highest sales?\n"
                "• What are my total sales?\n"
                "• How many products do I have?\n"
                "• Am I making a profit or loss?"
            )
        return JsonResponse({"answer": answer})
    except ValueError:
        return JsonResponse({"answer": "Invalid request. Please try again."}, status=400)


# ==========================================================
# RESTOCK APIS
# RestockOrder must be defined only in store/models.py.
# ==========================================================

@admin_required
@require_GET
def restock_low_stock_api(request):
    products = Product.objects.filter(
        stock__lte=F("min_stock")
    ).order_by("stock", "name")
    return JsonResponse({
        "success": True,
        "products": [{
            "id": p.id,
            "name": p.name,
            "sku": p.sku,
            "stock": p.stock,
            "min_stock": p.min_stock,
            "supplier": p.supplier or "",
        } for p in products],
    })


@admin_required
@require_GET
def restock_orders_api(request):
    orders = RestockOrder.objects.select_related("product").all()
    return JsonResponse({
        "success": True,
        "orders": [{
            "id": order.id,
            "product_id": order.product_id,
            "product_name": order.product.name,
            "quantity": order.quantity,
            "supplier": order.supplier or "",
            "expected_delivery": (
                order.expected_delivery.isoformat()
                if order.expected_delivery else ""
            ),
            "status": order.status,
            "stock_applied": order.stock_applied,
        } for order in orders],
    })


@admin_required
@require_POST
def restock_create_order(request):
    try:
        data = parse_json_body(request)
        supplier = str(data.get("supplier", "")).strip()
        delivery_text = data.get("expected_delivery") or None
        expected_delivery = date.fromisoformat(delivery_text) if delivery_text else None
        items = data.get("items", [])

        if not isinstance(items, list) or not items:
            return json_error("Add at least one product to the restock order.")

        created_ids = []
        with transaction.atomic():
            for item in items:
                product_id = int(item["product_id"])
                quantity = int(item["quantity"])
                if quantity <= 0:
                    return json_error("Restock quantity must be greater than zero.")
                product = Product.objects.select_for_update().get(pk=product_id)
                order = RestockOrder.objects.create(
                    product=product,
                    quantity=quantity,
                    supplier=supplier or product.supplier,
                    expected_delivery=expected_delivery,
                )
                created_ids.append(order.id)

        return JsonResponse({
            "success": True,
            "message": "Restock order created successfully.",
            "order_ids": created_ids,
        })
    except Product.DoesNotExist:
        return json_error("A selected product does not exist.")
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return json_error("Invalid restock order details.")

    
    
@admin_required
@require_GET
def restock_orders_api(request):
    orders = RestockOrder.objects.select_related("product").all()

    return JsonResponse({
        "success": True,
        "orders": [{
            "id": order.id,
            "product_id": order.product_id,
            "product_name": order.product.name,
            "quantity": order.quantity,
            "received_quantity": order.received_quantity,
            "supplier": order.supplier or "",
            "expected_delivery": (
                order.expected_delivery.isoformat()
                if order.expected_delivery else ""
            ),
            "status": order.status,
            "stock_applied": order.stock_applied,
            "created_at": timezone.localtime(
                order.created_at
            ).strftime("%d %b %Y"),
        } for order in orders],
    })


@admin_required
@require_POST
def restock_update_status(request, order_id):
    try:
        data = parse_json_body(request)
        new_status = data.get("status")

        allowed_statuses = {
            choice[0] for choice in RestockOrder.STATUS_CHOICES
        }

        if new_status not in allowed_statuses:
            return json_error("Invalid restock status.")

        with transaction.atomic():
            order = RestockOrder.objects.select_for_update().get(
                pk=order_id
            )

            if order.stock_applied:
                return json_error(
                    "Stock has already been added. This order cannot be changed."
                )

            if order.status == "Cancelled":
                return json_error("A cancelled order cannot be changed.")

            if order.status == "Received" and new_status != "Received":
                return json_error(
                    "A received order cannot be reverted."
                )

            if new_status == "Cancelled" and order.status == "Received":
                return json_error("A received order cannot be cancelled.")

            order.status = new_status
            order.save(update_fields=["status", "updated_at"])

        return JsonResponse({
            "success": True,
            "message": f"Order status updated to {new_status}.",
        })

    except RestockOrder.DoesNotExist:
        return json_error("Restock order not found.", 404)
    except ValueError:
        return json_error("Invalid request body.")


import json

from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.db import transaction

from .models import RestockOrder, Product



@admin_required
@require_POST
def add_received_stock(request, order_id):
    try:
        data = parse_json_body(request)

        # JavaScript must send this exact field name.
        received_quantity = int(data.get("received_quantity", 0))

        if received_quantity <= 0:
            return json_error(
                "Received quantity must be greater than zero."
            )

    except (ValueError, TypeError, OverflowError):
        return json_error(
            "Invalid quantity. Enter a whole number greater than zero."
        )

    with transaction.atomic():
        try:
            order = (
                RestockOrder.objects
                .select_for_update()
                .select_related("product")
                .get(pk=order_id)
            )
        except RestockOrder.DoesNotExist:
            return json_error("Purchase order not found.", 404)

        if order.status != "Received":
            return json_error(
                "Change the order status to Received before adding stock."
            )

        if order.stock_applied:
            return json_error(
                "Stock has already been added for this order."
            )

        if received_quantity > order.quantity:
            return json_error(
                f"Received quantity cannot exceed the ordered "
                f"quantity of {order.quantity}."
            )

        product = (
            Product.objects
            .select_for_update()
            .get(pk=order.product_id)
        )

        # Update product stock.
        product.stock += received_quantity
        product.save(update_fields=["stock", "updated_at"])

        # Mark this order as processed.
        order.received_quantity = received_quantity
        order.stock_applied = True
        order.save(update_fields=[
            "received_quantity",
            "stock_applied",
            "updated_at",
        ])

    return JsonResponse({
        "success": True,
        "message": (
            f"{received_quantity} units of "
            f"{product.name} added to stock successfully."
        ),
        "received_quantity": received_quantity,
        "new_stock": product.stock,
    })
