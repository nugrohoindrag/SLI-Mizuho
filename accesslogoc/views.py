import logging
import json
from datetime import datetime
from import_document.models import FileProcessings
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Q

from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from django.db.models import Min, Max

from import_cabang_oc.models import Branch, ConsumerOC
from .models import activity_logs_oc
from .serializers import ActivityLogSerializer, UploadLogSerializer

# ✅ Import helper
from AdminMenu.auth_helpers import user_has_permission, resolve_permission_alias

logger = logging.getLogger(__name__)

# 🔧 Helper agar lebih clean
def check_permission(user, alias):
    return user_has_permission(user, resolve_permission_alias(alias))

# 🔍 HTML View: Access Log
@login_required(login_url='/')
def access_log_oc(request):
    if not check_permission(request.user, 'view-access-log-oc'):
        return render(request, '403.html', status=403)
    return render(request, "content/modal/access-log-oc.html", {"user": request.user})

# 🔍 HTML View: Upload Log
@login_required(login_url='/')
def upload_log_oc(request):
    if not check_permission(request.user, 'view-upload-log-oc'):
        return render(request, '403.html', status=403)
    return render(request, "content/modal/upload-log.html", {"user": request.user})

# ✅ List cabang (branch)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def branch_list_oc(request):
    if not check_permission(request.user, 'view-upload-log-oc'):
        return Response({'message': 'Forbidden'}, status=403)
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
class ActivityLogListAPIViewOC(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not check_permission(request.user, 'view-access-log-oc'):
            return Response({'message': 'Forbidden'}, status=403)
        try:
            logs = activity_logs_oc.objects.select_related("causer").order_by("-created_at")
            serializer = ActivityLogSerializer(logs, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error("🔥 Gagal mengambil activity log: %s", str(e))
            return Response(
                {"detail": "Gagal mengambil activity log.", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

# ✅ Upload Log API
DATE_FMT = "%Y-%m-%d"

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def UploadLogListAPIViewOC(request):

    if not check_permission(request.user, 'view-upload-log-oc'):
        return Response({'message': 'Forbidden'}, status=403)

    try:
        draw   = int(request.GET.get('draw', 1))
        start  = int(request.GET.get('start', 0))
        length = int(request.GET.get('length', 10))
        search_value = (request.GET.get('search[value]', '') or '').strip()

        start_date   = request.GET.get('start_date') or None
        end_date     = request.GET.get('end_date') or None
        branch_name  = request.GET.get('branch_name') or None
        dt_columns = [
            'no',
            'modified_by__username',
            'file_name',
            'download_link',
            'status',
            'branch_name',
            'created_at',
        ]

        order_col_idx = int(request.GET.get('order[0][column]', 6) or 6)
        order_dir     = request.GET.get('order[0][dir]', 'desc')
        order_field   = dt_columns[order_col_idx] if 0 <= order_col_idx < len(dt_columns) else 'created_at'
        if order_field in ('no', 'download_link'):  # kolom virtual
            order_field = 'created_at'
        if order_dir == 'desc':
            order_field = f'-{order_field}'

        # base queryset
        qs_base = FileProcessings.objects.filter(category="2")
        records_total = qs_base.count()  # total sebelum filter tanggal/search
        qs = qs_base

        # filter tanggal
        if start_date:
            try:
                sd = datetime.strptime(start_date, DATE_FMT)
                qs = qs.filter(created_at__date__gte=sd.date())
            except ValueError:
                pass
        if end_date:
            try:
                ed = datetime.strptime(end_date, DATE_FMT)
                qs = qs.filter(created_at__date__lte=ed.date())
            except ValueError:
                pass

        # filter branch → map ke uploader_company
        if branch_name:
            qs = qs.filter(uploader_company__iexact=branch_name)

        # search global
        if search_value:
            qs = qs.filter(
                Q(file_name__icontains=search_value) |
                Q(status__icontains=search_value) |
                Q(uploader_company__icontains=search_value) |
                Q(modified_by__username__icontains=search_value) |
                Q(modified_by__name__icontains=search_value)  # kalau kolom name ada
            )

        records_filtered = qs.count()  # total setelah filter

        # sorting & pagination
        if order_field == 'created_at':
            qs = qs.order_by('-created_at')
        else:
            qs = qs.order_by(order_field)
        page = qs[start:start + length]

        # auto-number (no) mulai dari start
        counter = {'count': start}
        def next_counter():
            counter['count'] += 1
            return counter['count']

        serializer = UploadLogSerializer(
            page,
            many=True,
            context={
                'request': request,
                'index_start': start,
                'counter': next_counter
            }
        )

        return Response({
            'draw': draw,
            'recordsTotal': records_total,
            'recordsFiltered': records_filtered,
            'data': serializer.data,
        }, status=status.HTTP_200_OK)

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
class UploadLogRangeAPIViewOC(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not check_permission(request.user, 'view-upload-log-oc'):
            return Response({'message': 'Forbidden'}, status=403)
        try:
            logs = activity_logs_oc.objects.filter(description__in=['created', 'updated'])
            start_date = logs.aggregate(Min('created_at'))['created_at__min']
            end_date = logs.aggregate(Max('created_at'))['created_at__max']

            return Response({
                'start_date': start_date.date() if start_date else None,
                'end_date': end_date.date() if end_date else None
            }, status=200)

        except Exception as e:
            logger.exception("🔥 Gagal mengambil rentang tanggal upload log")
            return Response(
                {"detail": "Gagal mengambil rentang tanggal.", "error": str(e)},
                status=500
            )
