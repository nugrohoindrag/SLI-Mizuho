from rest_framework import serializers
from .models import ActivityLogUser
from import_document.models import ConsumerDocument

def extract_agreement_number(properties):
    if isinstance(properties, dict):
        return properties.get('agreement_number')
    elif isinstance(properties, list):
        for item in properties:
            if isinstance(item, dict) and 'agreement_number' in item:
                return item['agreement_number']
    return None

class AllActivityLogSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()
    branch_name = serializers.SerializerMethodField()
    filename = serializers.SerializerMethodField()
    subject_info = serializers.SerializerMethodField()
    timestamp = serializers.DateTimeField(source='created_at')
    menu_name = serializers.SerializerMethodField()

    class Meta:
        model = ActivityLogUser
        fields = [
            'id', 'log_name', 'description', 'subject_type', 'subject_info',
            'user_name', 'branch_name', 'filename', 'timestamp', 'menu_name'
        ]

    def get_user_name(self, obj):
        return obj.causer.username if obj.causer else 'System'

    def get_branch_name(self, obj):
        if obj.causer and hasattr(obj.causer, 'branch') and obj.causer.branch:
            return obj.causer.branch.branch_name
        return '-'

    def get_filename(self, obj):
        agreement_number = extract_agreement_number(obj.properties)

        if agreement_number:
            try:
                consumer_doc = ConsumerDocument.objects.filter(agreement_number=agreement_number).first()
                if consumer_doc:
                    return consumer_doc.file_url or '-'
            except Exception:
                pass  # Jangan print di production

        return '-'

    def get_subject_info(self, obj):
        return f"{obj.subject_type} (ID: {obj.subject_id})" if obj.subject_type and obj.subject_id else "-"

    def get_menu_name(self, obj):
        if isinstance(obj.properties, dict):
            return obj.properties.get('menu') or '-'
        return '-'


class UploadLogSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()
    filename = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    branch_name = serializers.SerializerMethodField()
    timestamp = serializers.DateTimeField(source='created_at')

    class Meta:
        model = ActivityLogUser
        fields = ['id', 'user_name', 'filename', 'status', 'branch_name', 'timestamp']

    def get_user_name(self, obj):
        return obj.causer.username if obj.causer else 'System'

    def get_filename(self, obj):
        agreement_number = extract_agreement_number(obj.properties)

        if agreement_number:
            try:
                consumer_doc = ConsumerDocument.objects.filter(agreement_number=agreement_number).first()
                if consumer_doc:
                    return consumer_doc.file_url or '-'
            except Exception:
                pass  # Hindari print di production

        return '-'

    def get_status(self, obj):
        return obj.description or '-'

    def get_branch_name(self, obj):
        if obj.causer and hasattr(obj.causer, 'branch') and obj.causer.branch:
            return obj.causer.branch.branch_name
        return '-'