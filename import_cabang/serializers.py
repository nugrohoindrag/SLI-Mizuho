from rest_framework import serializers
from .models import Consumer, Branch
from datetime import datetime, date
from accesslog.models import ActivityLog
import json, re, logging
from AdminNotaries.models import Notary
from django.db import transaction, IntegrityError, DatabaseError, connection
from prekonsumen.models import NotaryBranchArea
from AktaSertifikat.models import ConsumerStatus
from django.utils.functional import SimpleLazyObject
from django.db.models import Q
from django.utils import timezone
from psycopg2 import errorcodes

logger = logging.getLogger(__name__)

class ConsumerSerializer(serializers.ModelSerializer):
    branch_code = serializers.CharField(write_only=True, required=False, allow_null=True)
    consumerstatus = serializers.PrimaryKeyRelatedField(read_only=True)

    # read-only: tidak terima input 'branch', hanya tampilkan branch_code saat serialize
    branch = serializers.SlugRelatedField(
        read_only=True,
        slug_field='branch_code',
    )
    branch_name = serializers.CharField(read_only=True)

    # helper write-only
    customer_address_rt = serializers.CharField(write_only=True, required=False, allow_blank=True)
    customer_address_rw = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = Consumer
        extra_fields = ['branch_code', 'customer_address_rt', 'customer_address_rw']
        fields = (
            [f.name for f in model._meta.get_fields()
             if (not f.is_relation or f.one_to_one or (f.many_to_one and f.related_model))
             and f.name not in ['director_notary_name', 'notary_city']]
            + extra_fields
            + ['consumerstatus']
        )

    # --- validators & helpers ---

    # def validate_agreement_number(self, value):
    #     if value:
    #         qs = ConsumerOC.objects.filter(agreement_number=value)
    #         if self.instance:
    #             qs = qs.exclude(id=self.instance.id)
    #         if qs.exists():
    #             raise serializers.ValidationError("Agreement number already exists.")
    #     return value

    def _to_date(self, val):
        if isinstance(val, date) and not isinstance(val, datetime):
            return val
        if isinstance(val, datetime):
            return val.date()
        if isinstance(val, str):
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
                try:
                    return datetime.strptime(val.strip(), fmt).date()
                except Exception:
                    pass
        return None
    
    def _coerce_empty_to_none(self, data: dict):
        """
        Untuk semua field non-Char/Text/JSON/Slug, jika value '' → None.
        Ini mencegah NOT NULL / cast error saat DB expect NULL, bukan ''.
        """
        from django.db import models
        for f in Consumer._meta.get_fields():
            if not hasattr(f, 'attname'):
                continue
            name = f.attname
            if name not in data:
                continue
            v = data.get(name)

            # lewati CharField/TextField: '' boleh
            if isinstance(f, (models.CharField, models.TextField, models.JSONField, models.SlugField)):
                continue

            # Untuk FK, attname = '<field>_id'
            if isinstance(f, models.ForeignKey):
                if v == '':
                    data[name] = None
                continue

            # Non-char lain
            if v == '':
                data[name] = None
        return data

    def validate(self, data):
        data.pop('branch', None)
        # tanggal & angka akan dinormalisasi di create()
        # Cek kombinasi unik jika keduanya ada
        num = data.get('agreement_number')
        dt  = self._to_date(data.get('agreement_date')) if data.get('agreement_date') else None
        if num and dt:
            qs = Consumer.objects.filter(agreement_number=num, agreement_date=dt)
            if self.instance:
                qs = qs.exclude(id=self.instance.id)
            if qs.exists():
                raise serializers.ValidationError({
                    'agreement_number': "Combination of agreement_number + agreement_date already exists."
                })
        return data

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
        elif hasattr(user, 'branch') and getattr(user, 'branch', None):
            branch = user.branch

        if branch:
            validated_data['branch'] = branch
            validated_data['branch_name'] = branch.branch_name

    def _apply_rt_rw(self, validated_data):
        rt = self.initial_data.get('customer_address_rt')
        rw = self.initial_data.get('customer_address_rw')
        if rt or rw:
            validated_data['rt_rw'] = f"{(rt or '').strip()}/{(rw or '').strip()}".strip('/')
        validated_data.pop('customer_address_rt', None)
        validated_data.pop('customer_address_rw', None)

    def _inject_notary_city(self, validated_data):
        # Tidak menyimpan notary_city sesuai permintaan
        validated_data['notary_city'] = None
        return
    
    def _normalize_dates(self, data: dict):
        date_fields = [
            'bpkb_birth_date', 'birth_date', 'deed_date', 'decree_date',
            'director_birth_date', 'agreement_date', 'spb_date',
            'installment_start_date', 'installment_end_date',
            'upload_date', 'go_live_date', 'certificate_date'
        ]
        for field in date_fields:
            v = data.get(field)
            if v in (None, ''):
                continue
            parsed = self._to_date(v)
            if parsed:
                data[field] = parsed
        return data
    
    def _normalize_numbers(self, data: dict):
        for field in ['guarantee_value', 'service_fee', 'pnbp_fee']:
            v = data.get(field)
            if isinstance(v, str):
                cleaned = v.replace('Rp', '').replace('.', '').replace(',', '').strip()
                if cleaned == '':
                    data[field] = None
                else:
                    try:
                        data[field] = int(cleaned)
                    except ValueError:
                        # biarkan validator field yang menolak
                        pass
        return data

    # --- CRUD ---
    def create(self, validated_data):
        validated_data.pop('branch', None)
        branch_code = validated_data.pop('branch_code', None)
        user = self._extract_user()

        self._apply_branch(validated_data, branch_code, user)
        self._apply_rt_rw(validated_data)

        # Normalisasi input dulu
        self._normalize_dates(validated_data)
        self._normalize_numbers(validated_data)
        self._coerce_empty_to_none(validated_data)

        if not validated_data.get('upload_date'):
            validated_data['upload_date'] = timezone.now().date()

        try:
            with transaction.atomic():
                consumer = super().create(validated_data)

                # sisipkan status+log
                ConsumerStatus.objects.create(
                    consumer=consumer, status='Check', updated_by=user
                )
                ActivityLog.objects.create(
                    log_name='CONSUMER_OC_UPLOAD',
                    description='created',
                    causer=user,
                    causer_type='AdminUser.User',
                    subject_id=consumer.id,
                    subject_type='ConsumerOC',
                    properties={
                        "agreement_number": consumer.agreement_number,
                        "branch_name": consumer.branch_name,
                        "filename": self.context.get('filename', '-')
                    }
                )
                return consumer

        except IntegrityError as e:
            constraint = getattr(getattr(e, '__cause__', None), 'diag', None)
            constraint_name = getattr(constraint, 'constraint_name', None)
            logger.error(
                "[DB INTEGRITY ERROR] constraint=%s agmt=%s date=%s",
                constraint_name,
                validated_data.get('agreement_number'),
                validated_data.get('agreement_date')
            )
            raise serializers.ValidationError({
                'agreement_number': f"DB integrity hit: {constraint_name or 'unknown'}"
            })

        except DatabaseError as e:
            # Keluarkan detail psycopg2 supaya ketahuan field bermasalah
            cause = getattr(e, '__cause__', None)
            pgcode = getattr(cause, 'pgcode', None)
            pgerror = getattr(cause, 'pgerror', None)
            diag = getattr(cause, 'diag', None)
            detail = getattr(diag, 'message_detail', None) if diag else None
            column = getattr(diag, 'column_name', None) if diag else None
            constraint = getattr(diag, 'constraint_name', None) if diag else None

            logger.exception(
                "[DB ERROR] agmt=%s pgcode=%s constraint=%s column=%s detail=%s raw=%s",
                validated_data.get('agreement_number'), pgcode, constraint, column, detail, str(e)
            )

            # tampilkan info yang membantu (tanpa terlalu teknis)
            msg = detail or pgerror or str(e)
            raise serializers.ValidationError({
                'non_field_errors': [f"Database error: {msg}"]
            })

        except Exception as e:
            logger.exception(
                "[UNEXPECTED ERROR] agmt=%s raw=%s",
                validated_data.get('agreement_number'), str(e)
            )
            raise serializers.ValidationError({
                'non_field_errors': ["Terjadi error tak terduga saat menyimpan baris ini."]
            })

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
            raise serializers.ValidationError({
                'agreement_number': "Combination of agreement_number + agreement_date already exists."
            })