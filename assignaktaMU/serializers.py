from rest_framework import serializers
from import_cabang_mu.models import ConsumerMU, Branch
from accesslogmu.models import ActivityLogMU
from .models import VehicleUnit
from django.db import transaction, IntegrityError
from django.utils.dateparse import parse_date
from decimal import Decimal, InvalidOperation

class ConsumerSerializer(serializers.ModelSerializer):
    branch_code = serializers.CharField(write_only=True, required=False, allow_null=True)
    branch = serializers.PrimaryKeyRelatedField(
        queryset=Branch.objects.all(),
        required=False,
        allow_null=True
    )
    branch_name = serializers.CharField(read_only=True)
    customer_address_rt = serializers.CharField(write_only=True, required=False, allow_blank=True)
    customer_address_rw = serializers.CharField(write_only=True, required=False, allow_blank=True)
    vehicle_contract_numbers = serializers.SerializerMethodField()
    vehicle_count = serializers.SerializerMethodField()

    class Meta:
        model = ConsumerMU
        fields = [*ConsumerMU._meta.get_fields(include_parents=False)] + [
            'vehicle_contract_numbers',
            'vehicle_count',
        ]
        read_only_fields = ['created_at']

    def validate_agreement_number(self, value):
        if value:
            qs = ConsumerMU.objects.filter(agreement_number=value)
            if self.instance:
                qs = qs.exclude(id=self.instance.id)
            if qs.exists():
                raise serializers.ValidationError("Agreement number already exists (duplicate).")
        return value
    
    def get_vehicle_contract_numbers(self, obj):
        # Ambil kendaraan berdasarkan nomor kontrak / agreement
        units = VehicleUnit.objects.filter(contract_number=obj.agreement_number)
        return [unit.chassis_number if unit.chassis_number else unit.engine_number for unit in units]

    def get_vehicle_count(self, obj):
        return VehicleUnit.objects.filter(contract_number=obj.agreement_number).count()


    def validate(self, data):
        if 'created_at' in data:
            raise serializers.ValidationError({"created_at": "This field cannot be updated."})

        date_fields = [
            'bpkb_birth_date', 'birth_date', 'deed_date', 'decree_date',
            'director_birth_date', 'agreement_date', 'spb_date',
            'installment_start_date', 'installment_end_date', 'upload_date'
        ]
        int_fields = [
            'assembly_year', 'is_st_used', 'over_credit', 'sync',
            'tenor'
        ]
        decimal_fields = [
            'otr_value', 'pnbp_fee', 'service_fee', 'principal_loan', 'guarantee_value'
        ]

        for field in date_fields:
            val = data.get(field)
            if val and isinstance(val, str):
                parsed = parse_date(val)
                if not parsed:
                    raise serializers.ValidationError({field: "Date has wrong format. Use YYYY-MM-DD."})
                data[field] = parsed

        for field in int_fields:
            val = data.get(field)
            if val is not None and isinstance(val, str):
                try:
                    data[field] = int(float(val))
                except ValueError:
                    raise serializers.ValidationError({field: "A valid integer is required."})

        for field in decimal_fields:
            val = data.get(field)
            if val is not None and isinstance(val, str):
                try:
                    data[field] = Decimal(val)
                except InvalidOperation:
                    raise serializers.ValidationError({field: "A valid decimal is required."})

        return data

    def _set_branch_fields(self, validated_data):
        branch_code = validated_data.pop('branch_code', None)
        if branch_code:
            branch = Branch.objects.filter(branch_code=branch_code).first()
            if not branch:
                raise serializers.ValidationError({'branch_code': f"Branch with code '{branch_code}' not found."})
            validated_data['branch'] = branch
            validated_data['branch_name'] = branch.branch_name

        rt = self.initial_data.get('customer_address_rt')
        rw = self.initial_data.get('customer_address_rw')
        if rt or rw:
            validated_data['rt_rw'] = f"{rt or ''}/{rw or ''}".strip('/')

    def create(self, validated_data):
        agreement_number = validated_data.get('agreement_number')
        if ConsumerMU.objects.filter(agreement_number=agreement_number).exists():
            raise serializers.ValidationError({'agreement_number': "Agreement number already exists (duplicate)."})

        with transaction.atomic():
            self._set_branch_fields(validated_data)
            consumer = super().create(validated_data)

            try:
                ActivityLogMU.objects.create(
                    log_name='CONSUMER_UPLOAD',
                    description='created',
                    causer=self.context.get('request').user if self.context.get('request') else None,
                    causer_type='AdminUser.User',
                    subject_id=consumer.id,
                    subject_type='Consumer',
                    properties={
                        "agreement_number": consumer.agreement_number,
                        "branch_name": consumer.branch_name,
                        "filename": self.context.get('filename', '-')
                    }
                )
            except Exception:
                pass

            return consumer

    def update(self, instance, validated_data):
        validated_data.pop('created_at', None)
        self._set_branch_fields(validated_data)

        updated = super().update(instance, validated_data)

        try:
            ActivityLogMU.objects.create(
                log_name='CONSUMER_UPLOAD',
                description='updated',
                causer=self.context.get('request').user if self.context.get('request') else None,
                causer_type='AdminUser.User',
                subject_id=updated.id,
                subject_type='Consumer',
                properties={"message": "Consumer updated."}
            )
        except Exception:
            pass

        return updated

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request and request.method == 'GET':
            self.fields.pop('branch_code', None)
            self.fields.pop('customer_address_rt', None)
            self.fields.pop('customer_address_rw', None)