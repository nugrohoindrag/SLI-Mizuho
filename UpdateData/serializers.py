from rest_framework import serializers
from .models import TemporaryUpdate
from django.utils.timezone import localtime, is_naive, make_aware
from rest_framework.exceptions import ValidationError

class TemporaryUpdateSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.SerializerMethodField()
    approved_by_name = serializers.SerializerMethodField()
    uploaded_date = serializers.SerializerMethodField()
    updated_date = serializers.SerializerMethodField()
    can_approve = serializers.SerializerMethodField()

    class Meta:
        model = TemporaryUpdate
        fields = [
            'id', 'category', 'agreement_number', 'replacement_value',
            'is_approved', 'update_status', 'uploaded_by', 'uploaded_by_name',
            'approved_by', 'approved_by_name', 'created_at', 'updated_at',
            'uploaded_date', 'updated_date', 'can_approve',
        ]
        read_only_fields = ['created_at', 'updated_at']

    # ========== Custom Getters ==========
    def get_uploaded_by_name(self, obj):
        return getattr(obj.uploaded_by, 'name', None) if obj.uploaded_by else None
    
    def get_can_approve(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        return obj.uploaded_by_id != request.user.id

    def get_approved_by_name(self, obj):
        return getattr(obj.approved_by, 'name', None) if obj.approved_by else None

    def get_uploaded_date(self, obj):
        if obj.created_at:
            dt = obj.created_at
            if is_naive(dt):
                dt = make_aware(dt)
            return localtime(dt).date()
        return None

    def get_updated_date(self, obj):
        if obj.updated_at:
            dt = obj.updated_at
            if is_naive(dt):
                dt = make_aware(dt)
            return localtime(dt).date()
        return None

    # ========== Create ==========
    def create(self, validated_data):
        instance = TemporaryUpdate.objects.create(**validated_data)
        return instance

    # ========== Update ==========
    def update(self, instance, validated_data):
        request = self.context.get('request')  # Ambil user dari context

        is_approved = validated_data.get('is_approved', None)

        if is_approved and not instance.is_approved:
            if not request or not request.user or not request.user.is_authenticated:
                raise ValidationError("User must be authenticated to approve.")

            if instance.uploaded_by_id == request.user.id:
                raise ValidationError({"non_field_errors": ["Anda tidak dapat meng-approve data yang Anda upload sendiri."]})

            instance.approved_by = request.user  # Isi otomatis user approver

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()
        return instance