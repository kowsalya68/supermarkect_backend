import json
import uuid
from django.db.models import (
    Sum,
    F,
    Count,
    Q,
    DecimalField,
    ExpressionWrapper,
)
from django.db.models.functions import Coalesce
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import calendar
from django.core.paginator import Paginator

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login as auth_login, logout
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.db import models, transaction
from django.db.models import Sum, F, Count
from django.db.models.functions import TruncMonth
from django.utils import timezone

from .models import Product, Sale, PredictionHistory, Bill, BillItem
# ==========================================================
# LOGIN
# ==========================================================

def login(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)

        if user is not None:
            if user.is_superuser and user.is_active:
                auth_login(request, user)
                return redirect('dashboard')
            else:
                messages.error(request, 'Only administrator accounts can login.')
        else:
            messages.error(request, 'Invalid username or password.')

    return render(request, 'login.html')


# ==========================================================
# DASHBOARD PAGE
# ==========================================================


# ==========================================================
# DASHBOARD PAGE
# ==========================================================

@login_required(login_url="login")
def dashboard(request):
    return render(request, "dashboard.html")


# ==========================================================
# DASHBOARD API
# ==========================================================

@login_required(login_url="login")
def dashboard_api(request):
    today = timezone.localdate()

    # ------------------------------------------------------
    # 1. PRODUCT SUMMARY
    # ------------------------------------------------------

    total_products = Product.objects.count()

    total_categories = (
        Product.objects.values("category")
        .distinct()
        .count()
    )

    low_stock = Product.objects.filter(
        stock__gt=0,
        stock__lte=F("min_stock")
    ).count()

    out_of_stock = Product.objects.filter(stock=0).count()

    # ------------------------------------------------------
    # 2. TOTAL AND MONTHLY SALES
    # ------------------------------------------------------

    total_sales = (
        Sale.objects.aggregate(total=Sum("total_amount"))["total"]
        or Decimal("0.00")
    )

    current_month_sales = (
        Sale.objects.filter(
            sale_date__year=today.year,
            sale_date__month=today.month
        ).aggregate(total=Sum("total_amount"))["total"]
        or Decimal("0.00")
    )

    # Previous calendar month
    first_day_this_month = today.replace(day=1)
    last_day_previous_month = first_day_this_month - timedelta(days=1)

    previous_month_sales = (
        Sale.objects.filter(
            sale_date__year=last_day_previous_month.year,
            sale_date__month=last_day_previous_month.month
        ).aggregate(total=Sum("total_amount"))["total"]
        or Decimal("0.00")
    )

    if previous_month_sales > 0:
        sales_growth = (
            (float(current_month_sales) - float(previous_month_sales))
            / float(previous_month_sales)
        ) * 100
    elif current_month_sales > 0:
        sales_growth = 100.0
    else:
        sales_growth = 0.0

    # ------------------------------------------------------
    # 3. LAST SIX CALENDAR MONTHS
    # Include months with zero sales.
    # ------------------------------------------------------

    monthly_sales_data = []

    for offset in range(5, -1, -1):
        # Convert the current month to a month index, then
        # move backwards by the requested number of months.
        month_index = today.year * 12 + today.month - 1 - offset
        year = month_index // 12
        month = month_index % 12 + 1

        month_total = (
            Sale.objects.filter(
                sale_date__year=year,
                sale_date__month=month
            ).aggregate(total=Sum("total_amount"))["total"]
            or Decimal("0.00")
        )

        monthly_sales_data.append({
            "month": calendar.month_abbr[month] + " " + str(year),
            "sales": float(month_total),
        })

    # ------------------------------------------------------
    # 4. TOP FIVE PRODUCTS
    # ------------------------------------------------------

    products = (
        Product.objects.annotate(
            total_sales=Sum("sales__total_amount"),
            monthly_product_sales=Sum(
                "sales__total_amount",
                filter=Q(
                    sales__sale_date__year=today.year,
                    sales__sale_date__month=today.month
                )
            )
        )
        .order_by("-total_sales", "name")[:5]
    )

    product_data = []

    for product in products:
        product_data.append({
            "id": product.id,
            "name": product.name,
            "sku": product.sku,
            "category": product.category,
            "price": float(product.price or 0),
            "cost_price": float(product.cost_price or 0),
            "stock": product.stock,
            "min_stock": product.min_stock,
            "stock_status": product.stock_status,
            # The dashboard column is labelled Monthly Sales.
            "sales": float(product.monthly_product_sales or 0),
            "profit_per_unit": float(product.profit_per_unit or 0),
        })

    # ------------------------------------------------------
    # 5. MOST RECENT AI PREDICTION
    # ------------------------------------------------------

    prediction = (
        PredictionHistory.objects
        .order_by("-created_at")
        .first()
    )

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

    # ------------------------------------------------------
    # 6. JSON RESPONSE
    # ------------------------------------------------------

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
# PRODUCTS PAGE
# ==========================================================


@login_required(login_url='login')
def products(request):

    # 1. Get all products, newest first
    product_list = Product.objects.all().order_by("-created_at")

    # 2. Search by product name or SKU
    search_query = request.GET.get("search", "").strip()

    if search_query:
        product_list = product_list.filter(
            Q(name__icontains=search_query) |
            Q(sku__icontains=search_query)
        )

    # 3. Filter products by category
    selected_category = request.GET.get("category", "").strip()

    valid_categories = [
        choice[0] for choice in Product.CATEGORY_CHOICES
    ]

    if selected_category in valid_categories:
        product_list = product_list.filter(
            category=selected_category
        )
    else:
        selected_category = ""

    # 4. Calculate summary counts from ALL products,
    # not just the current search results or page
    all_products = Product.objects.all()

    total_products = all_products.count()

    in_stock = all_products.filter(
        stock__gt=models.F("min_stock")
    ).count()

    low_stock = all_products.filter(
        stock__gt=0,
        stock__lte=models.F("min_stock")
    ).count()

    out_of_stock = all_products.filter(
        stock__lte=0
    ).count()

    # 5. Paginate filtered products: maximum 10 per page
    paginator = Paginator(product_list, 10)

    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # 6. Get categories from Product model choices
    categories = [
        choice[0]
        for choice in Product.CATEGORY_CHOICES
    ]

    # 7. Send data to the template
    context = {
        "products": page_obj.object_list,
        "page_obj": page_obj,
        "paginator": paginator,

        "categories": categories,
        "selected_category": selected_category,
        "search_query": search_query,

        "total_products": total_products,
        "in_stock": in_stock,
        "low_stock": low_stock,
        "out_of_stock": out_of_stock,
    }

    return render(
        request,
        "products.html",
        context
    )

# ==========================================================
# ADD PRODUCT API
# ==========================================================

@require_POST
@login_required(login_url='login')
def add_product(request):

    try:
        name = request.POST.get("name")
        sku = request.POST.get("sku")
        category = request.POST.get("category")
        price = request.POST.get("price")
        cost_price = request.POST.get("cost_price", 0)
        stock = request.POST.get("stock", 0)
        min_stock = request.POST.get("min_stock", 10)
        supplier = request.POST.get("supplier", "")

        if not name:
            return JsonResponse({
                "success": False,
                "message": "Product name is required."
            }, status=400)

        if not sku:
            return JsonResponse({
                "success": False,
                "message": "Product SKU is required."
            }, status=400)

        if Product.objects.filter(sku=sku).exists():
            return JsonResponse({
                "success": False,
                "message": "SKU already exists."
            }, status=400)

        product = Product.objects.create(
            name=name,
            sku=sku,
            category=category,
            price=price,
            cost_price=cost_price,
            stock=stock,
            min_stock=min_stock,
            supplier=supplier
        )

        return JsonResponse({
            "success": True,
            "message": "Product added successfully.",
            "product": {
                "id": product.id,
                "name": product.name,
                "sku": product.sku,
                "category": product.category,
                "price": str(product.price),
                "cost_price": str(product.cost_price),
                "stock": product.stock,
                "min_stock": product.min_stock,
                "supplier": product.supplier,
                "status": product.stock_status
            }
        })

    except Exception as e:
        return JsonResponse({
            "success": False,
            "message": str(e)
        }, status=400)


# ==========================================================
# EDIT PRODUCT API
# ==========================================================

@require_POST
@login_required(login_url='login')
def edit_product(request, product_id):

    try:
        product = get_object_or_404(Product, id=product_id)

        product.name = request.POST.get("name", product.name)

        new_sku = request.POST.get("sku", product.sku)

        if Product.objects.filter(
            sku=new_sku
        ).exclude(
            id=product.id
        ).exists():

            return JsonResponse({
                "success": False,
                "message": "SKU already exists."
            }, status=400)

        product.sku = new_sku

        product.category = request.POST.get(
            "category",
            product.category
        )

        product.price = request.POST.get(
            "price",
            product.price
        )

        product.cost_price = request.POST.get(
            "cost_price",
            product.cost_price
        )

        product.stock = request.POST.get(
            "stock",
            product.stock
        )

        product.min_stock = request.POST.get(
            "min_stock",
            product.min_stock
        )

        product.supplier = request.POST.get(
            "supplier",
            product.supplier
        )

        product.save()

        return JsonResponse({
            "success": True,
            "message": "Product updated successfully.",
            "product": {
                "id": product.id,
                "name": product.name,
                "sku": product.sku,
                "category": product.category,
                "price": str(product.price),
                "cost_price": str(product.cost_price),
                "stock": product.stock,
                "min_stock": product.min_stock,
                "supplier": product.supplier,
                "status": product.stock_status
            }
        })

    except Exception as e:
        return JsonResponse({
            "success": False,
            "message": str(e)
        }, status=400)


# ==========================================================
# DELETE PRODUCT API
# ==========================================================

@require_POST
@login_required(login_url='login')
def delete_product(request, product_id):

    try:
        product = get_object_or_404(
            Product,
            id=product_id
        )

        product_name = product.name

        product.delete()

        return JsonResponse({
            "success": True,
            "message": f"{product_name} deleted successfully."
        })

    except Exception as e:
        return JsonResponse({
            "success": False,
            "message": str(e)
        }, status=400)


# ==========================================================
# SALES PAGE
# ==========================================================

# ============================================================
# SALES PAGE
# ============================================================

@login_required(login_url="login")
def sales(request):

    today = timezone.localdate()
    current_year = today.year

    # ---------------------------------------------
    # 1. GET ALL SALES
    # ---------------------------------------------

    all_sales = Sale.objects.select_related("product")

    # ---------------------------------------------
    # 2. SEARCH FILTER
    # ---------------------------------------------

    search = request.GET.get("search", "").strip()

    sales_list = all_sales.order_by("-sale_date", "-id")

    if search:
        search_filter = (
            Q(product__name__icontains=search)
            | Q(product__sku__icontains=search)
            | Q(customer_name__icontains=search)
        )

        # Sale ID is numeric, so search it using an exact match.
        if search.isdigit():
            search_filter |= Q(id=int(search))

        sales_list = sales_list.filter(search_filter)

    # ---------------------------------------------
    # 3. DATE FILTER
    # ---------------------------------------------

    date_filter = request.GET.get("date", "all")

    month_start = today.replace(day=1)

    if date_filter == "today":
        sales_list = sales_list.filter(
            sale_date__date=today
        )

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
        previous_month_end = month_start - timedelta(days=1)
        previous_month_start = previous_month_end.replace(day=1)

        sales_list = sales_list.filter(
            sale_date__date__gte=previous_month_start,
            sale_date__date__lte=previous_month_end,
        )

    else:
        date_filter = "all"

    # ---------------------------------------------
    # 4. PAYMENT FILTER
    # ---------------------------------------------

    payment_filter = request.GET.get("payment", "all")

    if payment_filter in ["Cash", "UPI", "Card"]:
        sales_list = sales_list.filter(
            payment_method=payment_filter
        )
    else:
        payment_filter = "all"

    # ---------------------------------------------
    # 5. PAGINATION - 10 RECORDS PER PAGE
    # ---------------------------------------------

    paginator = Paginator(sales_list, 10)

    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # The template loops over `sales`, which is the current page.
    sales = page_obj

    # ---------------------------------------------
    # 6. TOTAL SALES SUMMARY
    # ---------------------------------------------

    total_sales = (
        all_sales.aggregate(total=Sum("total_amount"))["total"] or 0
    )

    transaction_count = all_sales.count()

    average_sale = (
        float(total_sales) / transaction_count
        if transaction_count
        else 0
    )

    # ---------------------------------------------
    # 7. TODAY'S SALES
    # ---------------------------------------------

    today_sales = (
        all_sales.filter(
            sale_date__date=today
        ).aggregate(total=Sum("total_amount"))["total"] or 0
    )

    # ---------------------------------------------
    # 8. CURRENT MONTH SALES
    # ---------------------------------------------

    monthly_sales = (
        all_sales.filter(
            sale_date__date__gte=month_start,
            sale_date__date__lte=today,
        ).aggregate(total=Sum("total_amount"))["total"] or 0
    )

    # ---------------------------------------------
    # 9. PREVIOUS MONTH SALES
    # ---------------------------------------------

    previous_month_end = month_start - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)

    previous_month_sales = (
        all_sales.filter(
            sale_date__date__gte=previous_month_start,
            sale_date__date__lte=previous_month_end,
        ).aggregate(total=Sum("total_amount"))["total"] or 0
    )

    # ---------------------------------------------
    # 10. MONTHLY SALES GROWTH
    # ---------------------------------------------

    if previous_month_sales:
        sales_growth = (
            (
                float(monthly_sales)
                - float(previous_month_sales)
            )
            / float(previous_month_sales)
        ) * 100
    else:
        sales_growth = 0

    sales_growth = round(sales_growth, 2)

    # ---------------------------------------------
    # 11. MONTHLY SALES CHART - ALL 12 MONTHS
    # ---------------------------------------------

    monthly_chart = (
        all_sales.filter(
            sale_date__year=current_year
        )
        .annotate(month=TruncMonth("sale_date"))
        .values("month")
        .annotate(total=Sum("total_amount"))
        .order_by("month")
    )

    # Map each month number to its actual sales amount.
    monthly_totals = {
        item["month"].month: float(item["total"] or 0)
        for item in monthly_chart
    }

    # Always show January through December.
    chart_labels = [
        calendar.month_abbr[month]
        for month in range(1, 13)
    ]

    # Months without sales show 0 instead of disappearing.
    chart_values = [
        monthly_totals.get(month, 0)
        for month in range(1, 13)
    ]

    # ---------------------------------------------
    # 12. TEMPLATE CONTEXT
    # ---------------------------------------------

    context = {
        # Sales table and pagination
        "sales": sales,
        "page_obj": page_obj,

        # Search and filters
        "search": search,
        "date_filter": date_filter,
        "payment_filter": payment_filter,

        # Summary cards
        "total_sales": total_sales,
        "today_sales": today_sales,
        "monthly_sales": monthly_sales,
        "previous_month_sales": previous_month_sales,
        "sales_growth": sales_growth,
        "transaction_count": transaction_count,
        "average_sale": average_sale,

        # Chart data
        "chart_labels": chart_labels,
        "chart_values": chart_values,
    }

    return render(request, "sales.html", context)



# ==========================================================
# BILLING PAGE
# ==========================================================


# ============================================
# BILLING PAGE
# ============================================

@login_required(login_url='login')
def billing(request):
    products = Product.objects.filter(stock__gt=0).order_by('name')

    return render(request, 'billing.html', {
        'products': products,
    })


# ============================================
# COMPLETE BILL AND SAVE TO DATABASE
# ============================================

@login_required(login_url='login')
@require_POST
def complete_bill(request):
    try:
        data = json.loads(request.body)

        items = data.get('items', [])
        customer_name = str(data.get('customer_name', '')).strip()
        payment_method = data.get('payment_method', 'Cash')

        if not isinstance(items, list) or not items:
            return JsonResponse({
                'success': False,
                'message': 'Please add at least one product.'
            }, status=400)

        if payment_method not in ['Cash', 'UPI', 'Card']:
            return JsonResponse({
                'success': False,
                'message': 'Invalid payment method.'
            }, status=400)

        discount = Decimal(str(data.get('discount', '0')))
        amount_received = Decimal(str(data.get('amount_received', '0')))

        if not discount.is_finite() or not amount_received.is_finite():
            raise ValueError('Invalid amount.')

        if discount < 0 or amount_received < 0:
            return JsonResponse({
                'success': False,
                'message': 'Amounts cannot be negative.'
            }, status=400)

        # Combine duplicate product IDs.
        quantities = {}

        for item in items:
            product_id = int(item['product_id'])
            quantity = int(item['quantity'])

            if quantity <= 0:
                raise ValueError('Product quantity must be greater than zero.')

            quantities[product_id] = (
                quantities.get(product_id, 0) + quantity
            )

        with transaction.atomic():
            # Lock products to avoid selling the same stock concurrently.
            locked_products = Product.objects.select_for_update().filter(
                id__in=quantities.keys()
            ).order_by('id')

            product_map = {
                product.id: product for product in locked_products
            }

            if len(product_map) != len(quantities):
                return JsonResponse({
                    'success': False,
                    'message': 'One or more products no longer exist.'
                }, status=400)

            subtotal = Decimal('0.00')

            for product_id, quantity in quantities.items():
                product = product_map[product_id]

                if product.stock < quantity:
                    return JsonResponse({
                        'success': False,
                        'message': (
                            f'Insufficient stock for {product.name}. '
                            f'Available stock: {product.stock}.'
                        )
                    }, status=400)

                subtotal += product.price * quantity

            subtotal = subtotal.quantize(Decimal('0.01'))

            if discount > subtotal:
                return JsonResponse({
                    'success': False,
                    'message': 'Discount cannot exceed the subtotal.'
                }, status=400)

            taxable_amount = subtotal - discount
            tax = (taxable_amount * Decimal('0.05')).quantize(
                Decimal('0.01')
            )
            total = taxable_amount + tax

            if amount_received < total:
                return JsonResponse({
                    'success': False,
                    'message': 'Amount received is less than the total.'
                }, status=400)

            change_amount = amount_received - total

            # Save the main bill.
            bill = Bill.objects.create(
                bill_number=f"BILL-{uuid.uuid4().hex[:12].upper()}",
                customer_name=customer_name,
                subtotal=subtotal,
                discount=discount,
                tax=tax,
                total=total,
                payment_method=payment_method,
                amount_received=amount_received,
                change_amount=change_amount,
            )

            # Save items, reduce stock, and record sales.
            for product_id, quantity in quantities.items():
                product = product_map[product_id]
                price = product.price
                line_total = price * quantity

                BillItem.objects.create(
                    bill=bill,
                    product=product,
                    quantity=quantity,
                    price=price,
                    total=line_total,
                )

                product.stock -= quantity
                product.save(update_fields=['stock', 'updated_at'])

                # Keep the Sales Management page connected to billing.
                Sale.objects.create(
                    product=product,
                    quantity=quantity,
                    selling_price=price,
                    payment_method=payment_method,
                    customer_name=customer_name,
                )

        return JsonResponse({
            'success': True,
            'message': 'Bill completed successfully.',
            'bill_number': bill.bill_number,
            'date': timezone.localtime(bill.created_at).strftime(
                '%d %b %Y, %I:%M %p'
            ),
            'subtotal': str(bill.subtotal),
            'discount': str(bill.discount),
            'tax': str(bill.tax),
            'total': str(bill.total),
            'payment_method': bill.payment_method,
            'amount_received': str(bill.amount_received),
            'change_amount': str(bill.change_amount),
        })

    except (ValueError, TypeError, KeyError, InvalidOperation, json.JSONDecodeError):
        return JsonResponse({
            'success': False,
            'message': 'Invalid billing information. Please check your entries.'
        }, status=400)



# ==========================================================
# AI PREDICTION PAGE
# ==========================================================


# ==========================================================
# AI BUSINESS PREDICTION - CORRECTED LOGIC
# ==========================================================

import calendar
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum, F, Q, DecimalField, Value
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from .models import Product, Sale


def get_business_prediction():
    """
    Estimate next month's sales, product costs, profit/loss,
    stock demand, and business risk from recorded sales.

    This is a simple trend-based forecast, not a trained ML model.
    """

    today = timezone.localdate()
    zero = Decimal("0.00")

    money_field = DecimalField(
        max_digits=16,
        decimal_places=2
    )

    # ------------------------------------------------------
    # 1. NEXT MONTH
    # ------------------------------------------------------

    next_month_index = today.year * 12 + today.month

    next_year = next_month_index // 12
    next_month = next_month_index % 12 + 1

    next_month_days = calendar.monthrange(
        next_year, next_month
    )[1]

    forecast_month = (
        f"{calendar.month_name[next_month]} {next_year}"
    )

    # ------------------------------------------------------
    # 2. SELECT RECENT SALES
    # ------------------------------------------------------

    recent_start = today - timedelta(days=89)

    recent_queryset = Sale.objects.filter(
        sale_date__date__gte=recent_start,
        sale_date__date__lte=today,
    ).select_related("product")

    use_recent_period = recent_queryset.exists()

    if use_recent_period:
        sales_queryset = recent_queryset
    else:
        # Fallback for businesses without sales in the
        # most recent 90-day period.
        sales_queryset = Sale.objects.filter(
            sale_date__date__lte=today
        ).select_related("product")

    oldest_sale = sales_queryset.order_by(
        "sale_date"
    ).first()

    if oldest_sale:
        observed_days = max(
            (today - oldest_sale.sale_date.date()).days + 1,
            1
        )
    else:
        observed_days = 0

    # ------------------------------------------------------
    # 3. HISTORICAL SALES AND COST
    # ------------------------------------------------------

    cost_expression = F("quantity") * F(
        "product__cost_price"
    )

    totals = sales_queryset.aggregate(
        revenue=Coalesce(
            Sum("total_amount"),
            Value(zero),
            output_field=money_field,
        ),
        cost=Coalesce(
            Sum(cost_expression),
            Value(zero),
            output_field=money_field,
        ),
    )

    historical_revenue = totals["revenue"] or zero
    historical_cost = totals["cost"] or zero

    # ------------------------------------------------------
    # 4. NEXT-MONTH SALES AND COST ESTIMATE
    # ------------------------------------------------------

    if observed_days > 0:
        daily_revenue = (
            historical_revenue / Decimal(observed_days)
        )

        daily_cost = (
            historical_cost / Decimal(observed_days)
        )

        predicted_sales = daily_revenue * Decimal(
            next_month_days
        )

        predicted_cost = daily_cost * Decimal(
            next_month_days
        )
    else:
        predicted_sales = zero
        predicted_cost = zero

    predicted_sales = predicted_sales.quantize(
        Decimal("0.01")
    )

    predicted_cost = predicted_cost.quantize(
        Decimal("0.01")
    )

    # Revenue minus estimated product cost.
    # This is gross profit, not net profit.
    predicted_profit = (
        predicted_sales - predicted_cost
    ).quantize(Decimal("0.01"))

    if predicted_sales > 0:
        profit_margin = (
            predicted_profit / predicted_sales
        ) * Decimal("100")
    else:
        profit_margin = zero

    profit_margin = profit_margin.quantize(
        Decimal("0.01")
    )

    # ------------------------------------------------------
    # 5. RECENT SALES GROWTH
    # Compare two consecutive 30-day periods.
    # ------------------------------------------------------

    recent_30_start = today - timedelta(days=29)
    previous_30_start = today - timedelta(days=59)
    previous_30_end = today - timedelta(days=30)

    recent_30_sales = Sale.objects.filter(
        sale_date__date__gte=recent_30_start,
        sale_date__date__lte=today,
    ).aggregate(
        total=Coalesce(
            Sum("total_amount"),
            Value(zero),
            output_field=money_field,
        )
    )["total"] or zero

    previous_30_sales = Sale.objects.filter(
        sale_date__date__gte=previous_30_start,
        sale_date__date__lte=previous_30_end,
    ).aggregate(
        total=Coalesce(
            Sum("total_amount"),
            Value(zero),
            output_field=money_field,
        )
    )["total"] or zero

    if previous_30_sales > 0:
        predicted_growth = (
            (recent_30_sales - previous_30_sales)
            / previous_30_sales
        ) * Decimal("100")
    elif recent_30_sales > 0:
        predicted_growth = Decimal("100.00")
    else:
        predicted_growth = zero

    predicted_growth = predicted_growth.quantize(
        Decimal("0.01")
    )

    # ------------------------------------------------------
    # 6. RISK LEVEL
    # ------------------------------------------------------

    if observed_days == 0 or predicted_sales == 0:
        risk_level = "High"
    elif predicted_profit < 0:
        risk_level = "High"
    elif profit_margin < Decimal("10"):
        risk_level = "Medium"
    else:
        risk_level = "Low"

    # ------------------------------------------------------
    # 7. CONFIDENCE INDICATOR
    # A transparent data-coverage heuristic, not ML accuracy.
    # ------------------------------------------------------

    if observed_days == 0:
        confidence = Decimal("0.00")
    else:
        confidence = min(
            Decimal(observed_days) / Decimal("90")
            * Decimal("100"),
            Decimal("100"),
        ).quantize(Decimal("0.01"))

    # ------------------------------------------------------
    # 8. PRODUCT DEMAND AND STOCK RISK
    # ------------------------------------------------------

    product_sales_filter = Q(
        sales__sale_date__date__lte=today
    )

    if use_recent_period:
        product_sales_filter &= Q(
            sales__sale_date__date__gte=recent_start
        )

    products = Product.objects.annotate(
        units_sold=Coalesce(
            Sum(
                "sales__quantity",
                filter=product_sales_filter,
            ),
            Value(0),
        )
    ).order_by("name")

    high_demand_products = []
    loss_risk_products = []

    for product in products:
        units_sold = product.units_sold or 0

        if observed_days > 0:
            demand = (
                Decimal(units_sold)
                / Decimal(observed_days)
                * Decimal(next_month_days)
            )

            # Round up so fractional estimated demand
            # does not underestimate restocking needs.
            predicted_demand = int(
                demand.to_integral_value(
                    rounding="ROUND_CEILING"
                )
            )
        else:
            predicted_demand = 0

        current_stock = product.stock

        stock_gap = max(
            predicted_demand - current_stock,
            0
        )

        # Products that may need restocking.
        if stock_gap > 0:
            high_demand_products.append({
                "id": product.id,
                "name": product.name,
                "category": product.category,
                "current_stock": current_stock,
                "predicted_demand": predicted_demand,
                "stock_gap": stock_gap,
            })

        # Products whose current stock substantially
        # exceeds the estimated next-month demand.
        if (
            predicted_demand > 0
            and current_stock > predicted_demand * 1.5
        ):
            risk = (
                "High"
                if current_stock > predicted_demand * 2
                else "Medium"
            )

            loss_risk_products.append({
                "id": product.id,
                "name": product.name,
                "category": product.category,
                "current_stock": current_stock,
                "predicted_demand": predicted_demand,
                "risk": risk,
                "recommendation": (
                    "Review existing stock, expiry dates, "
                    "discount opportunities, and upcoming orders."
                ),
            })

    high_demand_products.sort(
        key=lambda item: item["stock_gap"],
        reverse=True,
    )

    loss_risk_products.sort(
        key=lambda item: (
            item["risk"] == "High",
            item["current_stock"] - item["predicted_demand"],
        ),
        reverse=True,
    )

    # ------------------------------------------------------
    # 9. INVESTMENT ALLOCATION
    # These are editable planning defaults, not AI advice.
    # ------------------------------------------------------

    allocation_items = [
        {
            "name": "Grocery",
            "percent": 30,
            "css_class": "allocation-grocery",
        },
        {
            "name": "Vegetables",
            "percent": 20,
            "css_class": "allocation-vegetables",
        },
        {
            "name": "Dairy",
            "percent": 15,
            "css_class": "allocation-dairy",
        },
        {
            "name": "Beverages",
            "percent": 10,
            "css_class": "allocation-beverages",
        },
        {
            "name": "Snacks",
            "percent": 10,
            "css_class": "allocation-snacks",
        },
        {
            "name": "Bakery",
            "percent": 5,
            "css_class": "allocation-bakery",
        },
        {
            "name": "Personal Care",
            "percent": 5,
            "css_class": "allocation-personal",
        },
        {
            "name": "Household",
            "percent": 5,
            "css_class": "allocation-household",
        },
    ]

    # ------------------------------------------------------
    # 10. RETURN TEMPLATE AND API DATA
    # ------------------------------------------------------

    return {
        "forecast_month": forecast_month,
        "predicted_sales": predicted_sales,
        "predicted_cost": predicted_cost,
        "predicted_profit": predicted_profit,
        "profit_margin": profit_margin,
        "predicted_growth": predicted_growth,
        "previous_sales": historical_revenue,
        "historical_sales": historical_revenue,
        "historical_cost": historical_cost,
        "confidence": confidence,
        "risk_level": risk_level,
        "observed_days": observed_days,
        "high_demand_products": high_demand_products,
        "loss_risk_products": loss_risk_products,
        "allocation_items": allocation_items,
    }


# ==========================================================
# AI PREDICTION PAGE
# ==========================================================

@login_required(login_url="login")
def aiprediction(request):
    data = get_business_prediction()

    context = {
        "forecast": data,
        "high_demand_products": data["high_demand_products"],
        "loss_risk_products": data["loss_risk_products"],
        "allocation_items": data["allocation_items"],
    }

    return render(
        request,
        "aiprediction.html",
        context,
    )


# ==========================================================
# AI PREDICTION JSON API
# ==========================================================

@login_required(login_url="login")
def aiprediction_api(request):
    data = get_business_prediction()

    # Convert Decimal values recursively for JSON.
    def make_json_safe(value):
        if isinstance(value, Decimal):
            return float(value)

        if isinstance(value, dict):
            return {
                key: make_json_safe(item)
                for key, item in value.items()
            }

        if isinstance(value, list):
            return [
                make_json_safe(item)
                for item in value
            ]

        return value

    return JsonResponse({
        "success": True,
        "forecast": make_json_safe(data),
    })




# ==========================================================
# AI BUSINESS PREDICTION - HELPER FUNCTION
# ==========================================================


def get_yearly_report_data(year):
    """
    Calculate monthly revenue, product cost, profit,
    transaction count, category sales and top products.
    """

    zero = Decimal("0.00")

    # All sales recorded during the selected year.
    year_sales = Sale.objects.filter(
        sale_date__year=year
    )

    # Cost is estimated using each product's current cost_price.
    cost_expression = ExpressionWrapper(
        F("quantity") * F("product__cost_price"),
        output_field=DecimalField(
            max_digits=14,
            decimal_places=2
        )
    )

    totals = year_sales.aggregate(
        revenue=Sum("total_amount"),
        cost=Sum(cost_expression),
    )

    total_revenue = totals["revenue"] or zero
    total_cost = totals["cost"] or zero
    total_profit = total_revenue - total_cost

    transaction_count = year_sales.count()

    average_monthly_sales = total_revenue / Decimal("12")

    profit_margin = (
        total_profit / total_revenue * Decimal("100")
        if total_revenue else zero
    )

    # Build all 12 months, including months with no sales.
    monthly_rows = []

    for month in range(1, 13):
        month_sales = year_sales.filter(
            sale_date__month=month
        )

        monthly_totals = month_sales.aggregate(
            revenue=Sum("total_amount"),
            cost=Sum(cost_expression),
        )

        revenue = monthly_totals["revenue"] or zero
        cost = monthly_totals["cost"] or zero
        profit = revenue - cost

        margin = (
            profit / revenue * Decimal("100")
            if revenue else zero
        )

        monthly_rows.append({
            "month_number": month,
            "month": calendar.month_name[month],
            "short_month": calendar.month_abbr[month],
            "sales": revenue,
            "cost": cost,
            "profit": profit,
            "margin": round(float(margin), 2),
            "transactions": month_sales.count(),
        })

    best_month = max(
        monthly_rows,
        key=lambda row: row["sales"],
        default=None
    )

    # Category performance from recorded sale items.
    category_rows = list(
        year_sales.values("product__category")
        .annotate(revenue=Sum("total_amount"))
        .order_by("-revenue")
    )

    category_data = []
    category_colors = [
        "#6478f5", "#20b486", "#f4a340",
        "#ec6b83", "#8b5cf6", "#17a2b8",
        "#e87932", "#64748b",
    ]

    for index, row in enumerate(category_rows):
        revenue = row["revenue"] or zero

        percentage = (
            float(revenue / total_revenue * Decimal("100"))
            if total_revenue else 0
        )

        category_data.append({
            "name": row["product__category"] or "Uncategorized",
            "revenue": revenue,
            "percentage": round(percentage, 1),
            "color": category_colors[index % len(category_colors)],
        })

    # Top 5 products by recorded sales revenue.
    top_products = list(
        year_sales.values(
            "product_id",
            "product__name",
            "product__category",
        )
        .annotate(
            revenue=Sum("total_amount"),
            units_sold=Sum("quantity"),
        )
        .order_by("-revenue")[:5]
    )

    for index, product in enumerate(top_products, start=1):
        product["rank"] = index
        product["name"] = product["product__name"]
        product["category"] = (
            product["product__category"] or "Uncategorized"
        )
        product["revenue"] = product["revenue"] or zero

    # Overall performance label.
    if total_revenue == 0:
        performance = "No Sales Data"
    elif profit_margin >= 20:
        performance = "Excellent"
    elif profit_margin >= 10:
        performance = "Good"
    elif profit_margin >= 0:
        performance = "Average"
    else:
        performance = "Loss"

    return {
        "year": year,
        "total_revenue": total_revenue,
        "total_cost": total_cost,
        "total_profit": total_profit,
        "average_monthly_sales": average_monthly_sales,
        "profit_margin": round(float(profit_margin), 2),
        "transaction_count": transaction_count,
        "best_month": best_month,
        "monthly_rows": monthly_rows,
        "category_data": category_data,
        "top_products": top_products,
        "performance": performance,
    }


# ==========================================================
# PREVIOUS YEAR SALES REPORT PAGE
# ==========================================================

@login_required(login_url="login")
def previousyear(request):
    current_year = timezone.localdate().year

    # Use the previous completed calendar year by default.
    default_year = current_year - 1

    try:
        selected_year = int(
            request.GET.get("year", default_year)
        )
    except (ValueError, TypeError):
        selected_year = default_year

    try:
        compare_year = int(
            request.GET.get("compare_year", selected_year - 1)
        )
    except (ValueError, TypeError):
        compare_year = selected_year - 1

    # Restrict the selectable year range.
    selected_year = max(2000, min(selected_year, current_year))
    compare_year = max(2000, min(compare_year, current_year))

    report = get_yearly_report_data(selected_year)
    comparison = get_yearly_report_data(compare_year)

    # Growth is based on revenue, not profit.
    if comparison["total_revenue"] > 0:
        growth = (
            (
                report["total_revenue"]
                - comparison["total_revenue"]
            )
            / comparison["total_revenue"]
        ) * Decimal("100")
    elif report["total_revenue"] > 0:
        growth = Decimal("100")
    else:
        growth = Decimal("0")

    # Prepare chart values for both years.
    chart_labels = [
        row["short_month"] for row in report["monthly_rows"]
    ]

    chart_current = [
        float(row["sales"]) for row in report["monthly_rows"]
    ]

    chart_previous = [
        float(row["sales"]) for row in comparison["monthly_rows"]
    ]

    available_years = range(current_year, 1999, -1)

    context = {
        "report": report,
        "comparison": comparison,
        "selected_year": selected_year,
        "compare_year": compare_year,
        "available_years": available_years,
        "growth": round(float(growth), 2),
        "chart_labels": chart_labels,
        "chart_current": chart_current,
        "chart_previous": chart_previous,
    }

    return render(
        request,
        "previousyear.html",
        context
    )


# ==========================================================
# EXPORT PREVIOUS YEAR REPORT AS CSV
# ==========================================================

@login_required(login_url="login")
def export_previousyear(request):
    current_year = timezone.localdate().year

    try:
        year = int(
            request.GET.get("year", current_year - 1)
        )
    except (ValueError, TypeError):
        year = current_year - 1

    year = max(2000, min(year, current_year))

    report = get_yearly_report_data(year)

    response = HttpResponse(
        content_type="text/csv; charset=utf-8"
    )

    response["Content-Disposition"] = (
        f'attachment; filename="sales_report_{year}.csv"'
    )

    # UTF-8 BOM helps Excel display the file correctly.
    response.write("\ufeff")

    writer = csv.writer(response)

    writer.writerow([f"Supermarket AI - Sales Report {year}"])
    writer.writerow([
        "Month",
        "Sales Revenue",
        "Estimated Cost",
        "Estimated Profit",
        "Profit Margin (%)",
        "Transactions",
    ])

    for row in report["monthly_rows"]:
        writer.writerow([
            row["month"],
            row["sales"],
            row["cost"],
            row["profit"],
            row["margin"],
            row["transactions"],
        ])

    writer.writerow([])
    writer.writerow([
        "TOTAL",
        report["total_revenue"],
        report["total_cost"],
        report["total_profit"],
        report["profit_margin"],
        report["transaction_count"],
    ])

    return response



# ==========================================================
# LOGOUT
# ==========================================================

def logout_view(request):
    logout(request)  # Ends the current user's session
    messages.success(request, "You have logged out successfully.")
    return redirect('login')


from datetime import datetime
from decimal import Decimal

from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from .models import Sale


@require_GET
def export_previousyear(request):
    # Validate the selected year
    current_year = timezone.localdate().year

    try:
        year = int(request.GET.get("year", current_year))
        if not 1900 <= year <= 9998:
            year = current_year
    except (TypeError, ValueError):
        year = current_year

    # Retrieve all sales for the selected year
    sales = list(
        Sale.objects.filter(sale_date__year=year)
        .select_related("product")
        .order_by("sale_date")
    )

    workbook = Workbook()

    # --------------------------------------------------
    # SHEET 1: YEAR SUMMARY
    # --------------------------------------------------
    summary = workbook.active
    summary.title = "Year Summary"
    summary.append(["Supermarket AI - Sales Report", year])
    summary.append(["Metric", "Value"])

    total_revenue = Decimal("0.00")
    total_cost = Decimal("0.00")
    total_units = 0

    monthly_data = {
        month: {
            "revenue": Decimal("0.00"),
            "cost": Decimal("0.00"),
            "transactions": 0,
        }
        for month in range(1, 13)
    }

    category_data = {}
    product_data = {}

    # --------------------------------------------------
    # SHEET 2: TRANSACTIONS
    # --------------------------------------------------
    transactions = workbook.create_sheet("Transactions")
    transactions.append([
        "Sale Date",
        "Product",
        "Category",
        "Quantity",
        "Selling Price",
        "Unit Cost",
        "Revenue",
        "Estimated Cost",
        "Estimated Profit",
        "Payment Method",
        "Customer",
    ])

    for sale in sales:
        product = sale.product
        quantity = sale.quantity or 0

        selling_price = Decimal(
            str(sale.selling_price or "0")
        )

        revenue = Decimal(
            str(
                sale.total_amount
                if sale.total_amount is not None
                else selling_price * quantity
            )
        )

        unit_cost = Decimal(
            str(product.cost_price or "0")
        )

        cost = unit_cost * quantity
        profit = revenue - cost

        category = product.category or "Uncategorized"
        month = sale.sale_date.month

        total_revenue += revenue
        total_cost += cost
        total_units += quantity

        monthly_data[month]["revenue"] += revenue
        monthly_data[month]["cost"] += cost
        monthly_data[month]["transactions"] += 1

        category_data.setdefault(category, Decimal("0.00"))
        category_data[category] += revenue

        product_id = product.pk

        if product_id not in product_data:
            product_data[product_id] = {
                "name": product.name,
                "category": category,
                "units": 0,
                "revenue": Decimal("0.00"),
            }

        product_data[product_id]["units"] += quantity
        product_data[product_id]["revenue"] += revenue

        payment = getattr(
            sale,
            "payment",
            getattr(sale, "payment_method", ""),
        )

        transactions.append([
            str(sale.sale_date),
            product.name,
            category,
            quantity,
            float(selling_price),
            float(unit_cost),
            float(revenue),
            float(cost),
            float(profit),
            str(payment),
            getattr(sale, "customer_name", "") or "",
        ])

    total_profit = total_revenue - total_cost

    profit_margin = (
        float(total_profit / total_revenue * 100)
        if total_revenue
        else 0
    )

    summary.append(["Total Revenue", float(total_revenue)])
    summary.append(["Estimated Cost", float(total_cost)])
    summary.append(["Estimated Profit", float(total_profit)])
    summary.append(["Profit Margin (%)", profit_margin])
    summary.append(["Sales Records", len(sales)])
    summary.append(["Total Units Sold", total_units])
    summary.append([
        "Average Monthly Sales",
        float(total_revenue / 12),
    ])

    # --------------------------------------------------
    # SHEET 3: MONTHLY REPORT
    # --------------------------------------------------
    monthly_sheet = workbook.create_sheet("Monthly Report")
    monthly_sheet.append([
        "Month",
        "Sales",
        "Estimated Cost",
        "Profit / Loss",
        "Profit Margin (%)",
        "Transactions",
    ])

    for month in range(1, 13):
        data = monthly_data[month]

        revenue = data["revenue"]
        cost = data["cost"]
        profit = revenue - cost

        margin = (
            float(profit / revenue * 100)
            if revenue
            else 0
        )

        month_name = datetime(
            year, month, 1
        ).strftime("%B")

        monthly_sheet.append([
            month_name,
            float(revenue),
            float(cost),
            float(profit),
            margin,
            data["transactions"],
        ])

    # --------------------------------------------------
    # SHEET 4: CATEGORY PERFORMANCE
    # --------------------------------------------------
    categories = workbook.create_sheet("Categories")
    categories.append([
        "Category",
        "Revenue",
        "Revenue Share (%)",
    ])

    for name, revenue in sorted(
        category_data.items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        percentage = (
            float(revenue / total_revenue * 100)
            if total_revenue
            else 0
        )

        categories.append([
            name,
            float(revenue),
            percentage,
        ])

    # --------------------------------------------------
    # SHEET 5: ALL PRODUCTS
    # --------------------------------------------------
    products = workbook.create_sheet("Products")
    products.append([
        "Rank",
        "Product Name",
        "Category",
        "Units Sold",
        "Revenue",
    ])

    ranked_products = sorted(
        product_data.values(),
        key=lambda item: item["revenue"],
        reverse=True,
    )

    for rank, item in enumerate(ranked_products, start=1):
        products.append([
            rank,
            item["name"],
            item["category"],
            item["units"],
            float(item["revenue"]),
        ])

    # --------------------------------------------------
    # FORMAT ALL WORKSHEETS
    # --------------------------------------------------
    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"

        for cell in sheet[1]:
            cell.font = Font(
                bold=True,
                color="FFFFFF",
            )
            cell.fill = PatternFill(
                fill_type="solid",
                fgColor="334155",
            )
            cell.alignment = Alignment(
                horizontal="center",
                vertical="center",
            )

        for column in sheet.columns:
            column_letter = get_column_letter(
                column[0].column
            )

            max_length = max(
                (
                    len(str(cell.value))
                    for cell in column
                    if cell.value is not None
                ),
                default=10,
            )

            sheet.column_dimensions[
                column_letter
            ].width = min(max(max_length + 3, 12), 35)

        if sheet.max_row >= 2:
            sheet.auto_filter.ref = sheet.dimensions

    # Style the summary title
    summary["A1"].font = Font(
        bold=True,
        size=16,
        color="1D4ED8",
    )

    # --------------------------------------------------
    # DOWNLOAD EXCEL FILE
    # --------------------------------------------------
    response = HttpResponse(
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        )
    )

    response["Content-Disposition"] = (
        f'attachment; filename="supermarket_report_{year}.xlsx"'
    )

    response["Cache-Control"] = "no-store"

    workbook.save(response)

    return response








import json
from decimal import Decimal

from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, F

from .models import Product, Sale


@login_required
@require_POST
def dashboard_chat_api(request):
    try:
        data = json.loads(request.body)
        question = data.get("message", "").strip().lower()

        if not question:
            return JsonResponse({
                "answer": "Please enter a question."
            }, status=400)

        # 1. Products that need restocking
        if any(word in question for word in [
            "restock", "low stock", "stock"
        ]):
            products = Product.objects.filter(
                stock__lte=F("min_stock")
            )

            if not products.exists():
                answer = "All products currently have sufficient stock."

            else:
                answer = "Products needing restocking:\n"
                for p in products[:10]:
                    answer += (
                        f"\n• {p.name}: {p.stock} units available "
                        f"(minimum: {p.min_stock})."
                    )

        # 2. Highest-selling products
        elif any(word in question for word in [
            "highest sales", "top products", "best selling",
            "most sold", "popular products"
        ]):
            products = (
                Sale.objects.values("product__name")
                .annotate(total_quantity=Sum("quantity"),
                          total_sales=Sum("total_amount"))
                .order_by("-total_sales")[:5]
            )

            if not products:
                answer = "No sales records are available yet."
            else:
                answer = "Top-selling products by sales value:\n"
                for i, p in enumerate(products, start=1):
                    answer += (
                        f"\n{i}. {p['product__name']} — "
                        f"₹{p['total_sales'] or 0} "
                        f"(quantity sold: {p['total_quantity'] or 0})."
                    )

        # 3. Total sales
        elif "sales" in question or "revenue" in question:
            total = Sale.objects.aggregate(
                amount=Sum("total_amount")
            )["amount"] or Decimal("0")

            answer = f"Total recorded sales are ₹{total:,.2f}."

        # 4. Product count
        elif "product" in question:
            count = Product.objects.count()
            answer = f"Your supermarket currently has {count} products."

        # 5. Profit-related questions (simple recorded estimate)
        elif "profit" in question or "loss" in question:
            result = Sale.objects.aggregate(
                revenue=Sum("total_amount"),
                cost=Sum(F("quantity") * F("product__cost_price"))
            )

            revenue = result["revenue"] or Decimal("0")
            cost = result["cost"] or Decimal("0")
            profit = revenue - cost

            if profit > 0:
                answer = (
                    f"Recorded estimated profit is ₹{profit:,.2f}. "
                    "This estimate uses recorded sales and product cost "
                    "and does not include other business expenses."
                )
            elif profit < 0:
                answer = (
                    f"Recorded estimated loss is ₹{abs(profit):,.2f}. "
                    "This estimate does not include other business expenses."
                )
            else:
                answer = (
                    "Recorded sales and product cost are currently equal, "
                    "or there are no sales records."
                )

        # 6. Help for other questions
        else:
            answer = (
                "I can answer questions about your supermarket records. "
                "Try asking:\n"
                "• Which products need restocking?\n"
                "• Which products have the highest sales?\n"
                "• What are my total sales?\n"
                "• How many products do I have?\n"
                "• Am I making a profit or loss?"
            )

        return JsonResponse({"answer": answer})

    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({
            "answer": "Invalid request. Please enter your question again."
        }, status=400)
