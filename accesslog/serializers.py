from rest_framework import serializers
from .models import ActivityLog
from django.utils.html import escape
from import_document.models import FileProcessings

# Tetap simpan kalau masih dipakai di tempat lain
class ActivityLogSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = ActivityLog
        fields = [
            "id", "log_name", "description", "subject_id", "subject_type",
            "causer_id", "causer_type", "properties", "created_at", "user_name"
        ]

    def get_user_name(self, obj):
        return obj.causer.username if getattr(obj, "causer", None) else "System"

class UploadLogSerializer(serializers.ModelSerializer):
    no = serializers.SerializerMethodField()
    user_name = serializers.SerializerMethodField()
    filename = serializers.SerializerMethodField()
    download_link = serializers.SerializerMethodField()
    status_badge = serializers.SerializerMethodField()
    branch_name = serializers.SerializerMethodField()
    timestamp = serializers.DateTimeField(source='created_at', format='%Y-%m-%d %H:%M:%S')

    class Meta:
        model = FileProcessings
        fields = [
            'no', 'user_name', 'filename', 'download_link',
            'status_badge', 'branch_name', 'timestamp'
        ]

    def get_no(self, obj):
        index_start = int(self.context.get('index_start', 0))
        counter = self.context.get('counter')
        return index_start + counter() if callable(counter) else index_start + 1

    def get_user_name(self, obj):
        user = getattr(obj, 'modified_by', None)
        if not user:
            return 'System'
        return getattr(user, 'username', None) or getattr(user, 'name', None) or 'System'

    def get_filename(self, obj):
        return obj.file_name or '-'

    def get_download_link(self, obj):
        url = obj.new_location or obj.old_path
        if not url:
            return '<span class="text-muted">-</span>'
        safe_url = escape(url)
        safe_name = escape(obj.file_name or safe_url.split('/')[-1])
        return f'<a href="{safe_url}" class="btn btn-sm btn-primary" target="_blank" rel="noopener"><i class="fas fa-download me-1"></i>{safe_name}</a>'

    def get_status_badge(self, obj):
        val = (obj.status or '').strip().lower()
        label = obj.status or '-'
        if val in ('done', 'success', 'completed', 'processed'):
            klass = 'badge bg-success'
        elif val in ('process', 'processing', 'in_progress'):
            klass = 'badge bg-warning text-dark'
        elif val in ('failed', 'error'):
            klass = 'badge bg-danger'
        else:
            klass = 'badge bg-secondary'
        return f'<span class="{klass}">{escape(label)}</span>'

    def get_branch_name(self, obj):
        user = getattr(obj, 'modified_by', None)
        if user and hasattr(user, 'branch') and user.branch:
            return getattr(user.branch, 'branch_name', '-') or '-'
        return '-'