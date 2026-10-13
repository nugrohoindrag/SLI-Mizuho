from rest_framework import serializers
from .models import ConsumerMU, Branch, ConsumerStatusMU
from datetime import date, datetime
from accesslogmu.models import ActivityLogMU
import json
from django.db import transaction, IntegrityError
from prekonsumen.models import NotaryBranchArea
from django.utils.functional import SimpleLazyObject
from django.db.models import Q
from django.utils import timezone
from AdminNotaries.models import Notary
from django.db.models import F, Value
from django.db.models.functions import Upper, Replace
from AktaSertifikat.models import ConsumerStatus

def _normalize_code(val: str) -> str:
    # Normalisasi: hilangkan spasi & dash, lalu uppercase
    return ''.join(str(val).split()).replace('-', '').upper()

def _norm_expr(field_name: str):
    # normalisasi di level DB untuk pencarian duplikat
    # UPPER(REPLACE(REPLACE(field, '-', ''), ' ', ''))
    return Upper(
        Replace(
            Replace(F(field_name), Value('-'), Value('')),
            Value(''),  # ganti spasi: Value(' ') -> Value('') dengan Replace berjenjang
            Value('')
        )
    )

class ConsumerSerializerMU(serializers.ModelSerializer):
    branch_code = serializers.CharField(write_only=True, required=False, allow_null=True)
    consumerstatusmu = serializers.PrimaryKeyRelatedField(read_only=True)

    # read-only supaya branch tidak diinput user
    branch = serializers.SlugRelatedField(
        read_only=True,
        slug_field='branch_code'
    )
    branch_name = serializers.CharField(read_only=True)

    # helper untuk rt/rw
    customer_address_rt = serializers.CharField(write_only=True, required=False, allow_blank=True)
    customer_address_rw = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = ConsumerMU
        extra_fields = ['branch_code', 'customer_address_rt', 'customer_address_rw']
        fields = (
            [f.name for f in model._meta.get_fields()
             if (not f.is_relation or f.one_to_one or (f.many_to_one and f.related_model))
             and f.name not in ['director_notary_name', 'notary_city']]  # exclude field sensitif
            + extra_fields
            + ['consumerstatusmu']
        )
        extra_kwargs = {
            'chassis_number': {'validators': []},
            'engine_number':  {'validators': []},
        }

    # ------------- helpers -------------

    def _to_date(self, val):
        if isinstance(val, date) and not isinstance(val, datetime):
            return val
        if isinstance(val, datetime):
            return val.date()
        if isinstance(val, str):
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
                try:
                    return datetime.strptime(val.strip(), fmt).date()
                except Exception:
                    pass
        return None

    def _extract_user(self):
        user = self.context['request'].user if 'request' in self.context else None
        if isinstance(user, SimpleLazyObject):
            user = user._wrapped
        return user

    def _apply_branch(self, validated_data, branch_code, user):
        branch = None
        if branch_code:
            branch = Branch.objects.filter(branch_code=branch_code).first()
            if not branch:
                raise serializers.ValidationError({'branch_code': f"Branch with code '{branch_code}' not found."})
        elif hasattr(user, 'branch') and user.branch:
            branch = user.branch

        if branch:
            validated_data['branch'] = branch
            validated_data['branch_name'] = branch.branch_name

        # selalu None
        validated_data['director_notary_name'] = None

    def _apply_rt_rw(self, validated_data):
        rt = self.initial_data.get('customer_address_rt')
        rw = self.initial_data.get('customer_address_rw')
        if rt or rw:
            validated_data['rt_rw'] = f"{(rt or '').strip()}/{(rw or '').strip()}".strip('/')
        validated_data.pop('customer_address_rt', None)
        validated_data.pop('customer_address_rw', None)

    def _inject_notary_city(self, validated_data):
        validated_data['notary_city'] = None
        return

    # ------------- uniqueness -------------

    def validate_chassis_number(self, value):
        if not value:
            return value
        norm = _normalize_code(value)
        qs = ConsumerMU.objects.annotate(norm=_norm_expr('chassis_number')).filter(norm=norm)
        if self.instance:
            qs = qs.exclude(id=self.instance.id)
        if qs.exists():
            raise serializers.ValidationError("Chassis number already exists.")
        return norm

    def validate_engine_number(self, value):
        if not value:
            return value
        norm = _normalize_code(value)
        qs = ConsumerMU.objects.annotate(norm=_norm_expr('engine_number')).filter(norm=norm)
        if self.instance:
            qs = qs.exclude(id=self.instance.id)
        if qs.exists():
            raise serializers.ValidationError("Engine number already exists.")
        return norm

    # ------------- global validate -------------

    def validate(self, data):
        # normalisasi pada partial update
        if 'chassis_number' in data and data['chassis_number']:
            data['chassis_number'] = _normalize_code(data['chassis_number'])
        if 'engine_number' in data and data['engine_number']:
            data['engine_number'] = _normalize_code(data['engine_number'])

        # parsing tanggal
        date_fields = [
            'bpkb_birth_date', 'birth_date', 'deed_date', 'decree_date',
            'director_birth_date', 'agreement_date', 'spb_date', 'go_live_date',
            'installment_start_date', 'installment_end_date', 'upload_date'
        ]
        for field in date_fields:
            if field in data and data[field] not in (None, ''):
                parsed = self._to_date(data[field])
                if not parsed:
                    raise serializers.ValidationError({field: "Date format must be a valid date."})
                data[field] = parsed

        # buang branch string dari payload
        data.pop('branch', None)
        return data

    # ------------- CRUD -------------

    def create(self, validated_data):
        validated_data.pop('branch', None)
        branch_code = validated_data.pop('branch_code', None)
        user = self._extract_user()

        self._apply_branch(validated_data, branch_code, user)
        self._apply_rt_rw(validated_data)
        self._inject_notary_city(validated_data)

        if not validated_data.get('upload_date'):
            validated_data['upload_date'] = timezone.now().date()

        try:
            with transaction.atomic():
                consumer = super().create(validated_data)

                ConsumerStatusMU.objects.create(
                    consumer=consumer,
                    status='Check',
                    updated_by=user
                )

                ActivityLogMU.objects.create(
                    log_name='CONSUMER_MU_UPLOAD',
                    description='created',
                    causer=user,
                    causer_type='AdminUser.User',
                    subject_id=consumer.id,
                    subject_type='ConsumerMU',
                    properties={
                        "agreement_number": consumer.agreement_number,
                        "branch_name": consumer.branch_name,
                        "filename": self.context.get('filename', '-')
                    }
                )
                return consumer
        except IntegrityError:
            raise serializers.ValidationError({'non_field_errors': "Duplicate chassis/engine detected by DB constraint."})

    def update(self, instance, validated_data):
        validated_data.pop('branch', None)
        branch_code = validated_data.pop('branch_code', None)
        user = self._extract_user()

        self._apply_branch(validated_data, branch_code, user)
        self._apply_rt_rw(validated_data)
        self._inject_notary_city(validated_data)

        try:
            return super().update(instance, validated_data)
        except IntegrityError:
            raise serializers.ValidationError({'non_field_errors': "Duplicate chassis/engine detected by DB constraint."})