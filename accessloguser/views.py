import logging, json

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt

from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes

from import_cabang.models import Branch, Consumer
from .models import ActivityLogUser
from .serializers import AllActivityLogSerializer, UploadLogSerializer
from django.utils.dateparse import parse_date
from django.db.models import Q

# ✅ Import helper
from AdminMenu.auth_helpers import user_has_permission, resolve_permission_alias, log_activity, log_action_cbv

logger = logging.getLogger(__name__)

# 🔧 Helper agar lebih clean
def check_permission(user, alias):
    return user_has_permission(user, resolve_permission_alias(alias))

# 🔍 HTML View: Access Log
@login_required(login_url='')
def access_log_user(request):
    if not check_permission(request.user, 'view_access_log_user'):
        return render(request, '403.html', status=403)
    return render(request, "content/modal/access-log-user.html", {"user": request.user})

# ✅ List cabang (branch)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def branch_list_user(request):
    try:
        branches = Branch.objects.all().values('branch_code', 'branch_name')
        return Response(list(branches), status=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"🔥 Gagal mengambil branch list: {str(e)}")
        return Response(
            {"detail": "Gagal mengambil daftar branch.", "error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

# ✅ Activity Log List API
class ActivityLogListAPIViewUser(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not check_permission(request.user, 'view_access_log_user'):
            return Response({'message': 'Forbidden'}, status=403)
        try:
            start_date = request.query_params.get('start_date')
            end_date = request.query_params.get('end_date')
            branch_name = request.query_params.get('branch_name')

            logs = ActivityLogUser.objects.select_related("causer").all()

            if start_date:
                logs = logs.filter(created_at__date__gte=start_date)

            if end_date:
                logs = logs.filter(created_at__date__lte=end_date)

            if branch_name:
                logs = logs.filter(
                    causer__branch__branch_name__iexact=branch_name
                )

            logs = logs.order_by("-created_at")
            serializer = AllActivityLogSerializer(logs, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.exception("🔥 Gagal mengambil activity log")
            return Response(
                {"detail": "Gagal mengambil activity log.", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

logger = logging.getLogger(__name__)

def _to_int(v, default):
    try:
        return int(v)
    except Exception:
        return default

class UploadLogListAPIViewUser(APIView):
    permission_classes = [IsAuthenticated]

    ORDERABLE_COLS = {
        'user_name': 'causer__username',
        'status': None,
        'branch_name': 'causer__branch__branch_name',
        'timestamp': 'created_at',
    }

    def get(self, request):
        if not check_permission(request.user, 'view_upload_log_user'):
            return Response({'message': 'Forbidden'}, status=403)

        try:
            draw      = _to_int(request.query_params.get('draw', 0), 0)
            start     = _to_int(request.query_params.get('start', 0), 0)
            length    = _to_int(request.query_params.get('length', 10), 10)
            search_q  = (request.query_params.get('search') or '').strip()
            order_col = (request.query_params.get('order_col') or 'timestamp').strip()
            order_dir = (request.query_params.get('order_dir') or 'desc').strip().lower()

            start_date = request.query_params.get('start_date')
            end_date   = request.query_params.get('end_date')
            branch_name = request.query_params.get('branch_name')

            # 1) jangan batasi ke 'created'/'updated' kalau memang mau lihat upload
            qs_base = ActivityLogUser.objects.select_related("causer")

            records_total = qs_base.count()

            qs = qs_base

            if start_date:
                d = parse_date(start_date)
                if d:
                    qs = qs.filter(created_at__date__gte=d)

            if end_date:
                d = parse_date(end_date)
                if d:
                    qs = qs.filter(created_at__date__lte=d)

            if branch_name:
                qs = qs.filter(causer__branch__branch_name__iexact=branch_name)

            if search_q:
                qs = qs.filter(
                    Q(causer__username__icontains=search_q) |
                    Q(causer__name__icontains=search_q) |
                    Q(causer__branch__branch_name__icontains=search_q) |
                    Q(properties__icontains=search_q)
                )

            records_filtered = qs.count()

            order_field = self.ORDERABLE_COLS.get(order_col, 'created_at')
            if order_field:
                if order_dir == 'desc':
                    order_field = '-' + order_field
                qs = qs.order_by(order_field)
            else:
                qs = qs.order_by('-created_at')

            page_objs = list(qs[start:start+length])

            serializer = UploadLogSerializer(page_objs, many=True)
            return Response({
                'draw': draw,
                'recordsTotal': records_total,
                'recordsFiltered': records_filtered,
                'data': serializer.data
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.exception("🔥 Gagal mengambil upload log")
            return Response(
                {"detail": "Gagal mengambil upload log.", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )