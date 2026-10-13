from django.contrib import admin

from .models import PnbpRate


@admin.register(PnbpRate)
class PnbpRateAdmin(admin.ModelAdmin):
    list_display = ("code_key", "label", "min_amount", "max_amount", "fee", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("code_key", "label")
    ordering = ("sort_order", "min_amount")

