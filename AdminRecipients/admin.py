from django.contrib import admin
from .models import Recipient

@admin.register(Recipient)
class RecipientAdmin(admin.ModelAdmin):
    list_display = (
        'business_entity_name',
        'decree_number',
        'tax_number',
        'phone_number',
        'address',
        'created_at',  # Ganti dari 'registration_date'
        'updated_at',
    )

    search_fields = (
        'business_entity_name',
        'decree_number',
        'tax_number',
        'phone_number',
        'address',
    )

    list_filter = (
        'created_at',  # Ganti dari 'registration_date'
        'updated_at',
    )

    fields = (
        'business_entity_name',
        'email',
        'phone_number',
        'address',
        'rt',
        'rw',
        'subdistrict',
        'district',
        'city',
        'province',
        'postal_code',
        'decree_number',
        'decree_date',
        'tax_number',
        'ahu_decree_number',
    )

    list_editable = (
        'phone_number',
        'address',
    )
