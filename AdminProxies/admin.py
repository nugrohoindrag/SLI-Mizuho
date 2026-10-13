from django.contrib import admin
from .models import AdminProxy

@admin.register(AdminProxy)
class AdminProxyAdmin(admin.ModelAdmin):
    list_display = (
        'full_name',
        'national_id',
        'birth_place',
        'birth_date',
        'province',
        'created_at',
    )
    search_fields = ('full_name', 'national_id', 'province')
    list_filter = ('province', 'created_at')
    ordering = ('-created_at',)
