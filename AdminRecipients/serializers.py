from rest_framework import serializers
from .models import Recipient
import re
from django.core.validators import RegexValidator

PHONE_RE   = re.compile(r'^(?:\+62|0)\d{8,13}$')
POSTAL_RE  = re.compile(r'^\d{5}$')

phone_validator  = RegexValidator(regex=PHONE_RE,
    message="Nomor telepon harus diawali +62 atau 0 dan mengandung 9-14 digit total.")
postal_validator = RegexValidator(regex=POSTAL_RE,
    message="Kode pos harus 5 digit angka.")

class RecipientSerializer(serializers.ModelSerializer):
    phone_number     = serializers.CharField(required=False, allow_blank=True, validators=[phone_validator])
    postal_code      = serializers.CharField(required=False, allow_blank=True, validators=[postal_validator])
    decree_date      = serializers.DateField(required=False, allow_null=True, input_formats=['%Y-%m-%d'])
    decree_date_end  = serializers.DateField(required=False, allow_null=True, input_formats=['%Y-%m-%d'])

    class Meta:
        model  = Recipient
        fields = '__all__'

    # normalisasi phone
    # def validate_phone_number(self, value):
    #     v = (value or '').strip()
    #     if v.startswith('0'):
    #         v = '+62' + v[1:]
    #     return v

    # kosongkan string → None untuk field tanggal
    def to_internal_value(self, data):
        data = data.copy()
        for f in ('decree_date', 'decree_date_end'):
            if data.get(f) == '':
                data[f] = None
        return super().to_internal_value(data)

    # fallback: jika decree_date_end kosong tapi decree_date ada → samakan
    def validate(self, attrs):
        start = attrs.get('decree_date')
        end   = attrs.get('decree_date_end')
        if start and not end:
            attrs['decree_date_end'] = start
        if start and end and end < start:
            raise serializers.ValidationError({'decree_date_end': 'Tidak boleh sebelum Tgl SK.'})
        return attrs

    def create(self, validated_data):
        return Recipient.objects.create(**validated_data)

    def update(self, instance, validated_data):
        for k, v in validated_data.items():
            setattr(instance, k, v)
        instance.save(update_fields=list(validated_data.keys()) or None)
        return instance