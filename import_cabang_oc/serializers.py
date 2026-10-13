from rest_framework import serializers
from .models import ConsumerOC, Branch, ConsumerStatusOC
from accesslogoc.models import activity_logs_oc
from datetime import date, datetime
from django.utils import timezone
from AdminNotaries.models import Notary
from django.db.models import Q
import json, logging
from django.db import transaction, IntegrityError
from prekonsumen.models import NotaryBranchArea
from django.utils.functional import SimpleLazyObject

logger = logging.getLogger(__name__)

class ConsumerSerializerOC(serializers.ModelSerializer):
    branch_code = serializers.CharField(write_only=True, required=False, allow_null=True)
    consumerstatusoc = serializers.PrimaryKeyRelatedField(read_only=True)

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
        model = ConsumerOC
        ref_name = 'ConsumerDocumentOC'
        extra_fields = ['branch_code', 'customer_address_rt', 'customer_address_rw']
        fields = (
            [f.name for f in model._meta.get_fields()
             if (not f.is_relation or f.one_to_one or (f.many_to_one and f.related_model))
             and f.name not in ['director_notary_name', 'notary_city']]
            + extra_fields
            + ['consumerstatusoc']
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
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
                try:
                    return datetime.strptime(val.strip(), fmt).date()
                except Exception:
                    pass
        return None

    def validate(self, data):
        data.pop('branch', None)

        date_fields = [
            'bpkb_birth_date', 'birth_date', 'deed_date', 'decree_date',
            'director_birth_date', 'agreement_date', 'spb_date',
            'installment_start_date', 'installment_end_date', 'upload_date',
            'go_live_date',
        ]
        for field in date_fields:
            if field in data and data[field] not in (None, ''):
                parsed = self._to_date(data[field])
                if not parsed:
                    raise serializers.ValidationError({field: "Date format must be a valid date."})
                data[field] = parsed

        num = data.get('agreement_number')
        dt  = data.get('agreement_date')
        if num:
            qs = ConsumerOC.objects.filter(agreement_number=num)
            if self.instance:
                qs = qs.exclude(id=self.instance.id)

            if dt is not None:
                if qs.filter(agreement_date=dt).exists():
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
        """
        Isi validated_data['branch'] dengan instance Branch (bukan string),
        set branch_name, dan paksa director_notary_name=None.
        """
        branch = None
        if branch_code:
            branch = Branch.objects.filter(branch_code=branch_code).first()
            if not branch:
                raise serializers.ValidationError({'branch_code': f"Branch with code '{branch_code}' not found."})
        elif hasattr(user, 'branch') and user.branch:
            branch = user.branch

        if branch:
            validated_data['branch'] = branch           # instance Branch
            validated_data['branch_name'] = branch.branch_name

        # sesuai requirement
        validated_data['director_notary_name'] = None

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

    # --- CRUD ---

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
                ConsumerStatusOC.objects.create(
                    consumer=consumer, status='Check', updated_by=user
                )
                activity_logs_oc.objects.create(
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
            # Log constraint name agar tahu yang nabrak apa
            constraint = getattr(getattr(e, '__cause__', None), 'diag', None)
            constraint_name = getattr(constraint, 'constraint_name', None)
            logger.error(f"[DB UNIQUE ERROR] constraint={constraint_name} data="
                        f"{validated_data.get('agreement_number')} / {validated_data.get('agreement_date')}")
            raise serializers.ValidationError({
                'agreement_number': f"DB unique hit: {constraint_name or 'unknown'}"
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