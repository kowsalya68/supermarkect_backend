
from decimal import Decimal

from django.db import models
from django.core.validators import MinValueValidator
from django.core.validators import MinValueValidator, RegexValidator

# ============================================================
# PRODUCT MODEL
# ============================================================

class Product(models.Model):

    CATEGORY_CHOICES = [
        ("Vegetables", "Vegetables"),
        ("Dairy", "Dairy"),
        ("Grocery", "Grocery"),
        ("Bakery", "Bakery"),
        ("Beverages", "Beverages"),
        ("Snacks", "Snacks"),
        ("Personal Care", "Personal Care"),
        ("Household", "Household"),
    ]

    name = models.CharField(max_length=100)
    sku = models.CharField(max_length=50, unique=True)

    category = models.CharField(
        max_length=50,
        choices=CATEGORY_CHOICES
    )

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))]
    )

    cost_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )

    stock = models.PositiveIntegerField(default=0)
    min_stock = models.PositiveIntegerField(default=10)
    supplier = models.CharField(max_length=100, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.sku})"

    @property
    def stock_status(self):
        if self.stock == 0:
            return "Out of Stock"
        if self.stock <= self.min_stock:
            return "Low Stock"
        return "In Stock"

    @property
    def profit_per_unit(self):
        return self.price - self.cost_price


# ============================================================
# SALE MODEL
# ============================================================

class Sale(models.Model):

    PAYMENT_CHOICES = [
        ("Cash", "Cash"),
        ("UPI", "UPI"),
        ("Card", "Card"),
    ]

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="sales"
    )

    quantity = models.PositiveIntegerField(
        validators=[MinValueValidator(1)]
    )

    selling_price = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00")
    )

    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_CHOICES
    )

    customer_mobile = models.CharField(
    max_length=10,
    validators=[
        RegexValidator(
            regex=r"^[0-9]{10}$",
            message="Enter a valid 10-digit mobile number."
        )
    ],
      null=True,
    blank=True,
)
    sale_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product.name} - {self.quantity}"

    def save(self, *args, **kwargs):
        self.total_amount = (
            Decimal(self.selling_price) * Decimal(self.quantity)
        )
        super().save(*args, **kwargs)


# ============================================================
# BILL MODEL
# ============================================================

class Bill(models.Model):

    PAYMENT_CHOICES = [
        ("Cash", "Cash"),
        ("UPI", "UPI"),
        ("Card", "Card"),
    ]

    bill_number = models.CharField(max_length=50, unique=True)
    customer_mobile = models.CharField(
    max_length=10,
    validators=[
        RegexValidator(
            regex=r"^[0-9]{10}$",
            message="Enter a valid 10-digit mobile number."
        )
    ],
     null=True,
    blank=True,
)

    subtotal = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    discount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    tax = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    total = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )

    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_CHOICES
    )

    amount_received = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    change_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.bill_number


# ============================================================
# BILL ITEM MODEL
# ============================================================

class BillItem(models.Model):

    bill = models.ForeignKey(
        Bill,
        on_delete=models.CASCADE,
        related_name="items"
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="bill_items"
    )

    quantity = models.PositiveIntegerField(
        validators=[MinValueValidator(1)]
    )

    price = models.DecimalField(max_digits=10, decimal_places=2)

    total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00")
    )

    def __str__(self):
        return f"{self.product.name} - {self.quantity}"

    def save(self, *args, **kwargs):
        self.total = Decimal(self.price) * Decimal(self.quantity)
        super().save(*args, **kwargs)


# ============================================================
# AI PREDICTION HISTORY MODEL
# ============================================================

class PredictionHistory(models.Model):

    STATUS_CHOICES = [
        ("Completed", "Completed"),
        ("Pending", "Pending"),
        ("Failed", "Failed"),
    ]

    prediction_date = models.DateTimeField(auto_now_add=True)
    prediction_month = models.CharField(max_length=30)

    previous_sales = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    predicted_sales = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    predicted_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    predicted_profit = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    predicted_growth = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("0.00")
    )
    confidence = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0.00")
    )

    risk_level = models.CharField(max_length=30, default="Low")

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="Completed"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.prediction_month} - ₹{self.predicted_sales}"

    @property
    def profit_margin(self):
        if self.predicted_sales == 0:
            return Decimal("0.00")

        return (
            self.predicted_profit / self.predicted_sales
        ) * Decimal("100")


# ============================================================
# RESTOCK ORDER MODEL
# ============================================================


# ============================================================
# RESTOCK ORDER MODEL
# ============================================================

class RestockOrder(models.Model):

    STATUS_CHOICES = [
        ("Ordered", "Ordered"),
        ("In Transit", "In Transit"),
        ("Received", "Received"),
        ("Cancelled", "Cancelled"),
    ]

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="restock_orders"
    )

    quantity = models.PositiveIntegerField(
        validators=[MinValueValidator(1)]
    )

    # Actual quantity received from the supplier
    received_quantity = models.PositiveIntegerField(default=0)

    supplier = models.CharField(max_length=100, blank=True)

    expected_delivery = models.DateField(
        null=True,
        blank=True
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="Ordered"
    )

    # Prevent the same order from adding stock more than once.
    stock_applied = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return (
            f"{self.product.name} - {self.quantity} units "
            f"({self.status})"
        )

    class Meta:
        ordering = ["-created_at"]

