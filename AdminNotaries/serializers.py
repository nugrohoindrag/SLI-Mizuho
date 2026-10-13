from rest_framework import serializers
from .models import Notary
from datetime import datetime, time, date

class NotarySerializer(serializers.ModelSerializer):
    register_date = serializers.SerializerMethodField()
    updated_at = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()

    class Meta:
        model = Notary
        fields = [
            'id',
            'firm_name',
            'full_name',
            'short_title',
            'full_title',
            'email',
            'phone_number',
            'firm_address',
            'city_name',
            'website',
            'status',
            'status_label',
            'register_date',
            'updated_at',
            'is_active',
        ]

    def get_register_date(self, obj):
        val = getattr(obj, 'register_date', None)
        if isinstance(val, datetime):
            return val.strftime('%Y-%m-%d %H:%M:%S')
        elif isinstance(val, date):
            return datetime.combine(val, time.min).strftime('%Y-%m-%d %H:%M:%S')
        return None

    def get_updated_at(self, obj):
        val = getattr(obj, 'updated_at', None)
        if isinstance(val, datetime):
            return val.strftime('%Y-%m-%d %H:%M:%S')
        elif isinstance(val, date):
            return datetime.combine(val, time.min).strftime('%Y-%m-%d %H:%M:%S')
        return None

    def get_status_label(self, obj):
        return obj.status_label