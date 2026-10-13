from rest_framework import serializers
import uuid
import os
from django.core.files.storage import default_storage
from .models import ConsumerDocumentPF
from django.utils.text import slugify

class ConsumerDocumentSerializer(serializers.ModelSerializer):
    # ---- Derived / computed fields ----
    upload_date     = serializers.SerializerMethodField()
    branch_code     = serializers.SerializerMethodField()
    branch_name     = serializers.SerializerMethodField()
    consumer_status = serializers.SerializerMethodField()
    customer_type   = serializers.SerializerMethodField()
    notes           = serializers.SerializerMethodField()

    consumer_name   = serializers.SerializerMethodField()
    agreement_date  = serializers.SerializerMethodField()

    # ---- Model / I/O fields ----
    modified_by = serializers.ReadOnlyField(source='modified_by.username')
    file        = serializers.FileField(write_only=True, required=False)  # diterima, tapi diabaikan (view yang handle)
    file_url    = serializers.CharField(read_only=True)
    created_at  = serializers.DateTimeField(read_only=True)
    updated_at  = serializers.DateTimeField(read_only=True)

    class Meta:
        model  = ConsumerDocumentPF
        ref_name = 'ConsumerDocumentPF'
        fields = [
            'id', 'agreement_number', 'consumer_name', 'agreement_date',
            'document_type', 'description', 'notes', 'file_url',
            'branch_code', 'branch_name', 'consumer_status', 'customer_type',
            'modified_by', 'created_at', 'updated_at', 'file', 'upload_date',
        ]
        read_only_fields = [
            'file_url', 'branch_code', 'branch_name',
            'consumer_status', 'customer_type',
            'modified_by', 'created_at', 'updated_at', 'upload_date', 'notes'
        ]

    # ===================== helpers =====================
    def _get_consumer(self, obj):
        """
        Ambil objek Consumer dari consumer_map (sudah di-inject via context di ViewSet).
        """
        cm = self.context.get('consumer_map') or {}
        return cm.get(getattr(obj, 'agreement_number', None))

    def _derive_consumer_name_from_consumer(self, consumer):
        """
        Tentukan nama konsumen yang ditampilkan berdasarkan fiducia_provider.
        """
        if not consumer:
            return None
        fp     = (getattr(consumer, 'fiducia_provider', '') or '').upper()
        debtor = getattr(consumer, 'new_debtor_name', '') or getattr(consumer, 'debtor_name', '')
        bpkb   = getattr(consumer, 'new_name_on_bpkb', '') or getattr(consumer, 'name_on_bpkb', '')
        if fp in ('BPKB', 'ATAS NAMA BPKB'):
            return bpkb or debtor
        if fp in ('CUSTOMER', 'ATAS NAMA CUSTOMER'):
            return debtor or bpkb
        return debtor or bpkb or None

    # ===================== derived getters =====================
    def get_consumer_name(self, obj):
        return self._derive_consumer_name_from_consumer(self._get_consumer(obj))

    def get_agreement_date(self, obj):
        c = self._get_consumer(obj)
        d = getattr(c, 'agreement_date', None) if c else None
        if not d:
            return None
        try:
            # cocok untuk <input type="date">
            return d.strftime('%Y-%m-%d')
        except Exception:
            return str(d)

    def get_upload_date(self, obj):
        dt = getattr(obj, 'created_at', None)
        try:
            return dt.date().isoformat() if dt else None
        except Exception:
            return None

    def get_consumer_status(self, obj):
        consumer = self._get_consumer(obj)
        return getattr(consumer, 'status', None) or 'uncomplete'

    def get_customer_type(self, obj):
        consumer = self._get_consumer(obj)
        ct = getattr(consumer, 'customer_type', None)
        return (ct or '').strip().upper()

    def get_branch_code(self, obj):
        consumer = self._get_consumer(obj)
        if not consumer:
            return None
        if hasattr(consumer, 'branch') and getattr(consumer.branch, 'branch_code', None):
            return consumer.branch.branch_code
        return getattr(consumer, 'branch_code', None)

    def get_branch_name(self, obj):
        consumer = self._get_consumer(obj)
        if not consumer:
            return None
        if hasattr(consumer, 'branch') and getattr(consumer.branch, 'branch_name', None):
            return consumer.branch.branch_name
        return getattr(consumer, 'branch_name', None)

    def get_notes(self, obj):
        return getattr(obj, 'notes', '') or ''

    # ===================== create / update =====================
    def _safe_customer_filename_part(self, agreement_number):
        """
        Tetap dipertahankan jika suatu saat dibutuhkan penamaan file konsisten.
        (Saat ini upload diserahkan ke ViewSet.partial_update)
        """
        dummy = type('Obj', (), {'agreement_number': agreement_number})
        name  = self.get_consumer_name(dummy)
        return slugify(name or 'unknown')

    def create(self, validated_data):
        """
        Tidak menyimpan file di serializer.
        File dihandle di ViewSet.partial_update (PATCH) agar satu sumber kebenaran.
        """
        validated_data.pop('file', None)  # abaikan file di sini
        notes_input = self.initial_data.get('notes')
        instance = ConsumerDocumentPF.objects.create(**validated_data)

        if notes_input is not None:
            instance.notes = notes_input

        req = self.context.get('request')
        if req and hasattr(instance, 'modified_by') and req.user.is_authenticated:
            instance.modified_by = req.user

        instance.save()
        return instance

    def update(self, instance, validated_data):
        """
        Tidak menyimpan file di serializer.
        File dihandle di ViewSet.partial_update (PATCH).
        """
        validated_data.pop('file', None)  # abaikan file di sini
        notes_input = self.initial_data.get('notes', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if notes_input is not None:
            instance.notes = notes_input

        req = self.context.get('request')
        if req and hasattr(instance, 'modified_by') and req.user.is_authenticated:
            instance.modified_by = req.user

        instance.save()
        return instance