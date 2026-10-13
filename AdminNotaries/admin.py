from django.contrib import admin
from .models import Notary

@admin.register(Notary)
class NotaryAdmin(admin.ModelAdmin):
    list_display = ('firm_name', 'full_name', 'email', 'status', 'is_active', 'register_date')
    search_fields = ('firm_name', 'full_name', 'email', 'city_name')
    list_filter = ('status', 'is_active', 'city_name')
