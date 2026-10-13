from rest_framework import serializers
from .models import AdminProxy

class AdminProxySerializer(serializers.ModelSerializer):
    # ===== Alias field untuk DataTables/UI =====
    short_degree = serializers.CharField(source='short_title', read_only=True, allow_null=True)
    long_degree  = serializers.CharField(source='full_title',  read_only=True, allow_null=True)
    ktp_number   = serializers.CharField(source='national_id', read_only=True, allow_null=True)

    birth_place = serializers.SerializerMethodField(read_only=True)
    birth_date  = serializers.SerializerMethodField(read_only=True)
    province    = serializers.SerializerMethodField(read_only=True)
    city        = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = AdminProxy
        fields = [
            'id',
            'full_name',
            'birth_info',
            'nationality',
            'residence',
            'address',
            'rt',
            'rw',
            'subdistrict',
            'district',
            'province_id',
            'national_id',
            'notary_id',
            'is_active',
            'created_at',
            'updated_at',
            'full_title',
            'short_title',
            'active_from',
            'active_until',

            # === alias utk DataTables ===
            'short_degree',
            'long_degree',
            'ktp_number',
            'birth_place',
            'birth_date',
            'province',
            'city',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    # ---- helper alias ----
    def get_birth_place(self, obj):
        if obj.birth_info:
            parts = [p.strip() for p in str(obj.birth_info).split(',')]
            return parts[0] if parts else None
        return None

    def get_birth_date(self, obj):
        if obj.birth_info:
            parts = [p.strip() for p in str(obj.birth_info).split(',')]
            return parts[1] if len(parts) > 1 else None
        return None

    def get_province(self, obj):
        # jika province_id FK → return obj.province.name
        # jika bukan FK, tampilkan apa adanya
        try:
            return obj.province.name
        except Exception:
            return getattr(obj, 'province_id', None)

    def get_city(self, obj):
        # pakai field yang ada di model kamu (residence/city)
        return getattr(obj, 'city', None) or getattr(obj, 'residence', None)