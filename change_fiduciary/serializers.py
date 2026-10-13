from rest_framework import serializers
from .models import change_of_fiduciary
from import_cabang.models import Consumer
from django.utils.timezone import now
from decimal import Decimal, InvalidOperation
from django.db import models

# DateField custom: toleran ke '', 'null', 'None', '0000-00-00'
from rest_framework import serializers
from django.utils.timezone import now

# --- tetap gunakan SafeDateField & DATE_INPUT_FORMATS kamu
class SafeDateField(serializers.DateField):
    def to_internal_value(self, value):
        if value in [None, '', 'null', 'None', '0000-00-00', '0000/00/00', '00-00-0000', '00/00/0000']:
            return None
        return super().to_internal_value(value)

class SafeDecimalField(serializers.DecimalField):
    def _clean_str(self, v):
        # normalisasi: buang "Rp"/"IDR", spasi, ribuan, ubah koma -> titik
        s = str(v).strip()
        if not s or s.lower() in ('null', 'none'):
            return None
        s = s.replace('Rp', '').replace('IDR', '')
        s = s.replace(' ', '').replace(',', '.')
        # hapus pemisah ribuan titik kalau ada pola 1.234.567,89 -> 1234567.89
        # (biarkan titik terakhir sebagai desimal)
        if s.count('.') > 1:
            parts = s.split('.')
            s = ''.join(parts[:-1]) + '.' + parts[-1]
        return s

    def to_internal_value(self, data):
        if data in (None, ''):
            if self.allow_null:
                return None
            self.fail('null')
        s = self._clean_str(data)
        if s is None:
            return None if self.allow_null else super().to_internal_value(data)
        try:
            d = Decimal(s)
        except (InvalidOperation, ValueError):
            self.fail('invalid')
        # handle NaN/Infinity -> None
        if not d.is_finite():
            return None
        # quantize ke jumlah decimal_places
        q = Decimal('1').scaleb(-self.decimal_places)  # contoh: '0.01'
        try:
            d = d.quantize(q)
        except InvalidOperation:
            # fallback: biarkan DRF lakukan validasi max_digits/decimal_places
            pass
        return super().to_internal_value(str(d))

    def to_representation(self, value):
        if value is None:
            return None
        try:
            d = Decimal(value)
        except (InvalidOperation, ValueError):
            return None
        if not d.is_finite():
            return None
        return super().to_representation(d)

class SafeModelSerializer(serializers.ModelSerializer):
    def build_standard_field(self, field_name, model_field):
        field_class, field_kwargs = super().build_standard_field(field_name, model_field)
        if isinstance(model_field, models.DecimalField):
            field_class = SafeDecimalField
            # pastikan default aman
            field_kwargs.setdefault('required', False)
            field_kwargs.setdefault('allow_null', True)
            field_kwargs.setdefault('max_digits', model_field.max_digits)
            field_kwargs.setdefault('decimal_places', model_field.decimal_places)
        return field_class, field_kwargs

DATE_INPUT_FORMATS = [
    '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d',
    '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S',
    '%-d/%-m/%Y', '%#d/%#m/%Y',
]

class ChangeOfFiduciarySerializer(SafeModelSerializer):
    uploader = serializers.SerializerMethodField()
    branch_code_display = serializers.SerializerMethodField()
    branch_name_display = serializers.SerializerMethodField()

    # dropdown utama/rinci
    choose_change = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    choose_change_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    choose_change_name_org = serializers.CharField(required=False, allow_blank=True, allow_null=True, write_only=True)
    choose_mistake_2nama = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    # angka & tanggal
    # Pakai SafeDecimalField (kalau ada DecimalField lain di model, otomatis ikut aman)
    new_otr_values = SafeDecimalField(max_digits=20, decimal_places=2, required=False, allow_null=True)

    birth_date = SafeDateField(required=False, allow_null=True, input_formats=DATE_INPUT_FORMATS)
    bpkb_birth_date = SafeDateField(required=False, allow_null=True, input_formats=DATE_INPUT_FORMATS)
    date_of_change_of_fiduciary_letter = SafeDateField(required=False, allow_null=True, input_formats=DATE_INPUT_FORMATS)
    agreement_date = SafeDateField(required=False, allow_null=True, input_formats=DATE_INPUT_FORMATS)
    spb_date = SafeDateField(required=False, allow_null=True, input_formats=DATE_INPUT_FORMATS)
    installment_start_date = SafeDateField(required=False, allow_null=True, input_formats=DATE_INPUT_FORMATS)
    installment_end_date = SafeDateField(required=False, allow_null=True, input_formats=DATE_INPUT_FORMATS)
    deed_date = SafeDateField(required=False, allow_null=True, input_formats=DATE_INPUT_FORMATS)
    new_birth_date = SafeDateField(required=False, allow_null=True, input_formats=DATE_INPUT_FORMATS)
    new_deed_date = SafeDateField(required=False, allow_null=True, input_formats=DATE_INPUT_FORMATS)
    director_birth_date = SafeDateField(required=False, allow_null=True, input_formats=DATE_INPUT_FORMATS)
    decree_date = SafeDateField(required=False, allow_null=True, input_formats=DATE_INPUT_FORMATS)
    upload_date = SafeDateField(required=False, allow_null=True, input_formats=DATE_INPUT_FORMATS)

    class Meta:
        model = change_of_fiduciary
        fields = '__all__'
        read_only_fields = ('uploader',)

    # ---------- Helpers ----------
    def get_uploader(self, obj):
        return getattr(obj.uploader, 'username', '') if getattr(obj, 'uploader', None) else ''

    def get_branch_code_display(self, obj):
        if getattr(obj, 'branch', None):
            return getattr(obj.branch, 'branch_code', '') or getattr(obj.branch, 'code', '')
        return ''

    def get_branch_name_display(self, obj):
        if getattr(obj, 'branch', None):
            return getattr(obj.branch, 'branch_name', '') or getattr(obj.branch, 'name', '')
        return getattr(obj, 'branch_name', '') or ''

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        for f in [
            'birth_date','bpkb_birth_date','date_of_change_of_fiduciary_letter',
            'agreement_date','spb_date','installment_start_date','installment_end_date',
            'deed_date','new_birth_date','new_deed_date','director_birth_date',
            'decree_date','upload_date'
        ]:
            v = rep.get(f)
            if isinstance(v, str) and len(v) >= 10:
                rep[f] = v[:10]
        return rep

    # ---------- Validation (SATU method saja) ----------
    def validate(self, data):
        errors = {}

        # --- Agreement number
        agreement_number = (data.get('agreement_number') or '').strip().upper()
        if not agreement_number:
            raise serializers.ValidationError({'agreement_number': 'Nomor perjanjian wajib diisi.'})
        data['agreement_number'] = agreement_number

        if change_of_fiduciary.objects.filter(agreement_number__iexact=agreement_number).exists() and not self.instance:
            raise serializers.ValidationError({'agreement_number': 'Nomor perjanjian ini sudah pernah diproses ke perubahan fidusia.'})

        # --- helper ambil raw dari initial_data (tahan duplikat)
        qd = getattr(self, 'initial_data', {})
        def pick_last_non_empty(*keys):
            vals = []
            if hasattr(qd, 'getlist'):
                for k in keys:
                    vals += qd.getlist(k)
            else:
                for k in keys:
                    v = qd.get(k)
                    if v is not None:
                        vals.append(v)
            vals = [str(v).strip() for v in vals if str(v).strip() not in ('', 'null', 'None')]
            return vals[-1] if vals else ''

        # --- baca & norm nilai utama
        raw_change_type       = (data.get('change_type') or '').strip().lower()
        raw_choose_change     = pick_last_non_empty('choose_change') or (data.get('choose_change') or '')
        raw_choose_change     = raw_choose_change.strip().lower().replace(' ', '')
        raw_choose_change_name = pick_last_non_empty('choose_change_name', 'choose_change_name_org') \
                                or (data.get('choose_change_name') or '')
        raw_choose_change_name = raw_choose_change_name.strip().lower().replace(' ', '')

        # --- validasi & set choose_change (hanya 2 nilai yang valid)
        if raw_choose_change not in ('perubahan1', 'perubahan2'):
            # boleh kosong jika bukan subject; kalau subject wajib
            if raw_change_type == 'subject':
                errors['choose_change'] = 'Jenis perubahan subjek wajib dipilih.'
            data['choose_change'] = None
        else:
            data['choose_change'] = raw_choose_change

        # --- aturan bisnis untuk subject
        if raw_change_type == 'subject':
            if data.get('choose_change') == 'perubahan1':
                # WAJIB pilih detail nama dan disimpan PERSIS seperti dropdown ('perubahan1nama' / 'perubahan2nama')
                if raw_choose_change_name == '':
                    errors['choose_change_name'] = 'Jenis perubahan nama wajib dipilih (1→2 atau 2→1).'
                else:
                    allowed = {'perubahan1nama', 'perubahan2nama'}
                    if raw_choose_change_name not in allowed:
                        errors['choose_change_name'] = 'Nilai tidak valid. Pilih salah satu opsi yang tersedia.'
                    else:
                        data['choose_change_name'] = raw_choose_change_name
            else:
                # untuk "Kesalahan Penulisan" (perubahan2) tidak perlu detail nama
                data['choose_change_name'] = None

        # --- aturan bisnis untuk object
        if raw_change_type == 'object':
            for field in ['chassis_number', 'engine_number', 'type']:
                if not data.get(field):
                    errors[field] = f"{field.replace('_',' ').capitalize()} wajib diisi untuk perubahan objek."

        if errors:
            raise serializers.ValidationError(errors)
        return data

    # ---------- Create / Update ----------
    def create(self, validated_data):
        # buang field write_only agar tidak dilempar ke ORM
        validated_data.pop('choose_change_name_org', None)

        request = self.context.get("request")
        if request and getattr(request, 'user', None) and not validated_data.get("uploader"):
            validated_data["uploader"] = request.user
        if not validated_data.get("upload_date"):
            validated_data["upload_date"] = now().date()

        skip_if_blank_fields = [
            'new_debtor_name', 'new_address', 'new_id_number',
            'new_birth_place', 'new_birth_date',
            'new_chassis_number', 'new_engine_number',
            'new_type', 'new_color', 'new_brand',
            'new_deed_number', 'new_deed_date',
            'new_agreement_number', 'debtor_type'
        ]
        for f in list(validated_data.keys()):
            if f in skip_if_blank_fields:
                val = validated_data.get(f)
                if (val is None) or (isinstance(val, str) and val.strip() in ['', 'null', 'None']):
                    validated_data.pop(f)

        instance = super().create(validated_data)
        try:
            from assignaktaPF.signals import _create_or_update_legal_document
            _create_or_update_legal_document(instance)
        except Exception:
            pass
        return instance

    def update(self, instance, validated_data):
        validated_data.pop('choose_change_name_org', None)

        request = self.context.get("request")
        if request and getattr(request, 'user', None) and not validated_data.get("uploader"):
            validated_data["uploader"] = request.user

        skip_if_blank_fields = [
            'new_debtor_name', 'new_address', 'new_id_number',
            'new_birth_place', 'new_birth_date',
            'new_chassis_number', 'new_engine_number',
            'new_type', 'new_color', 'new_brand',
            'new_deed_number', 'new_deed_date',
            'new_agreement_number', 'debtor_type'
        ]
        for f in list(validated_data.keys()):
            if f in skip_if_blank_fields:
                val = validated_data.get(f)
                if (val is None) or (isinstance(val, str) and val.strip() in ['', 'null', 'None']):
                    validated_data.pop(f)

        instance = super().update(instance, validated_data)
        try:
            from assignaktaPF.signals import _create_or_update_legal_document
            _create_or_update_legal_document(instance)
        except Exception:
            pass
        return instance