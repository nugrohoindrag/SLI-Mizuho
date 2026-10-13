from rest_framework import serializers
from .models import LegalDocumentOC
from import_cabang_oc.models import ConsumerOC
from import_document_oc.models import ConsumerDocumentOC
from django.db import models
import os

class LegalDocumentSerializer(serializers.ModelSerializer):
    consumer_id = serializers.PrimaryKeyRelatedField(
        queryset=ConsumerOC.objects.all(), source='consumer', required=False, allow_null=True
    )
    consumer = serializers.SerializerMethodField()
    agreement_number = serializers.SerializerMethodField()
    branch_code = serializers.SerializerMethodField()
    branch_name = serializers.SerializerMethodField()

    class Meta:
        model = LegalDocumentOC
        ref_name = 'LegalDocumentOC'
        fields = [
            'id', 'consumer_id', 'consumer', 'agreement_number',
            'branch_code', 'branch_name',
            'document_number', 'document_date', 'document_time', 'notary',
            'power_receiver_name', 'ahu', 'voucher_code', 'certificate_number',
            'certificate_date', 'power_user', 'certificate_path', 'pnbp_path',
            'voucher_input', 'certificate_input', 'payment_date', 'business_entity',
            'business_entity_director', 'service_fee', 'pnbp_fee',
            'registration_date', 'billing_id', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Tangani single instance, list, atau queryset
        if isinstance(self.instance, models.QuerySet):
            instances = list(self.instance)
        elif self.instance:
            instances = [self.instance]
        else:
            instances = []

        document_numbers = [
            doc.document_number.strip().lower()
            for doc in instances
            if getattr(doc, 'document_number', None)
        ]

        self._doc_map = {}
        for d in ConsumerDocumentOC.objects.filter(file_url__isnull=False):
            basename = os.path.basename(d.file_url or '').replace(' ', '').lower()
            for num in document_numbers:
                if num in basename:
                    self._doc_map[num] = d

        agreement_numbers = [d.agreement_number for d in self._doc_map.values() if d.agreement_number]
        self._consumer_map = {
            c.agreement_number: c
            for c in ConsumerOC.objects.filter(agreement_number__in=agreement_numbers).select_related('branch')
        }

    def _get_doc(self, obj):
        # Ambil dokumen berdasarkan pencocokan document_number
        if not obj.document_number:
            return None
        num = obj.document_number.strip().lower()
        return self._doc_map.get(num)

    def _get_consumer(self, agreement_number):
        if not agreement_number:
            return None
        return self._consumer_map.get(agreement_number)

    def get_consumer(self, obj):
        try:
            return obj.consumer.debtor_name if obj.consumer else "-"
        except Exception as e:
            print("get_consumer error:", e)
            return "-"

    def get_agreement_number(self, obj):
        try:
            return obj.consumer.agreement_number if obj.consumer else None
        except Exception as e:
            print("get_agreement_number error:", e)
            return None

    def get_branch_code(self, obj):
        try:
            agreement_number = self.get_agreement_number(obj)
            consumer = self._get_consumer(agreement_number)
            return consumer.branch.branch_code if consumer and consumer.branch else "-"
        except Exception as e:
            print("get_branch_code error:", e)
            return "-"

    def get_branch_name(self, obj):
        try:
            agreement_number = self.get_agreement_number(obj)
            consumer = self._get_consumer(agreement_number)
            return consumer.branch.branch_name if consumer and consumer.branch else "-"
        except Exception as e:
            print("get_branch_name error:", e)
            return "-"
    
    def validate(self, data):
        document_number = (data.get('document_number') or getattr(self.instance, 'document_number', '')).strip().upper()

        if not document_number:
            raise serializers.ValidationError("Field 'document_number' tidak boleh kosong.")

        qs = LegalDocumentOC.objects.filter(document_number__iexact=document_number)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.exists():
            raise serializers.ValidationError({
                "document_number": f"LegalDocument dengan nomor '{document_number}' sudah ada."
            })

        return data
    
    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep['document_number'] = ''
        return rep