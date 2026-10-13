from rest_framework import serializers
from .models import Permission

class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = [
            'id',
            'name',
            'display_name',
            'description',
            'created_at',
            'updated_at'
        ]