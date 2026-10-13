import json
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_http_methods
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db import connection, transaction, IntegrityError

from .models import Permission
from .serializers import PermissionSerializer

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import api_view, permission_classes

# ✅ Import helper permission
from AdminMenu.auth_helpers import permission_required, user_has_permission, log_action_fbv

# ✅ View page untuk Admin
@login_required(login_url='')
@permission_required('view_permission')
def view_permission_users_admin(request):
    return render(request, 'content/page-users-permission.html', {
        'permissions': Permission.objects.all()
    })

# ✅ API: List permission (DRF)
@permission_classes([IsAuthenticated])
class PermissionListView(APIView):
    def get(self, request):
        if not user_has_permission(request.user, 'view_permission'):
            return render(request, '403.html', status=403)

        permissions = Permission.objects.all()
        serializer = PermissionSerializer(permissions, many=True)
        return Response(serializer.data)

# ✅ Generate ID (utility)
def get_next_id():
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT COALESCE(
                (SELECT MIN(t1.id) + 1
                 FROM permissions t1
                 WHERE NOT EXISTS (SELECT 1 FROM permissions t2 WHERE t2.id = t1.id + 1)),
                1
            ) AS next_id
        """)
        row = cursor.fetchone()
    return row[0]

# ✅ Reset sequence (utility)
def reset_activity_logs_sequence():
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT setval(
                pg_get_serial_sequence('activity_logs', 'id'),
                COALESCE(MAX(id), 1),
                true
            ) FROM activity_logs;
        """)
        result = cursor.fetchone()
    return result[0]

# ✅ Create permission
@login_required(login_url='')
@permission_required('create_permission')
@log_action_fbv("Create Permission", subject_type="Permission")
def create_permission(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'Method tidak diizinkan.'}, status=405)

    try:
        data = json.loads(request.body or "{}")

        raw_name = data.get('name')
        name = (raw_name or "").strip()

        print("========== CREATE PERMISSION DEBUG ==========")
        print("[RAW NAME]     :", repr(raw_name))
        print("[STRIPPED NAME]:", repr(name))
        data['name'] = name

        exists_exact = Permission.objects.filter(name=name).exists()
        exists_iexact = Permission.objects.filter(name__iexact=name).exists()

        print("[EXISTS EXACT ]:", exists_exact)
        print("[EXISTS IEXACT]:", exists_iexact)
        print("[TOTAL COUNT] :", Permission.objects.count())

        if exists_iexact:
            print("❌ BLOCKED BY PRE-CHECK (iexact)")
            return JsonResponse(
                {'message': 'Permission dengan nama ini sudah ada.'},
                status=409
            )

        with transaction.atomic():
            print("➡️  TRYING TO SAVE...")
            serializer = PermissionSerializer(data=data)

            if not serializer.is_valid():
                print("❌ SERIALIZER INVALID:", serializer.errors)
                return JsonResponse({'message': serializer.errors}, status=400)

            serializer.save()
            print("✅ SAVE SUCCESS")

        return JsonResponse({'message': 'Permission berhasil ditambahkan.'}, status=201)

    except IntegrityError as e:
        print("❌ INTEGRITY ERROR:", str(e))
        return JsonResponse(
            {'message': 'Permission dengan nama ini sudah ada.'},
            status=409
        )

    except Exception as e:
        print("❌ UNEXPECTED ERROR:", str(e))
        return JsonResponse(
            {'message': f'Error: {str(e)}'},
            status=500
        )

# ✅ Update permission
@require_http_methods(["PUT"])
@login_required(login_url='')
@permission_required('edit_permission')
@log_action_fbv("Update Permission", subject_type="Permission")
def update_permission(request, id):
    try:
        data = json.loads(request.body or "{}")
        name = (data.get('name') or "").strip()

        if Permission.objects.filter(name=name).exclude(id=id).exists():
            return JsonResponse({'message': 'Permission dengan nama ini sudah ada.'}, status=409)

        with transaction.atomic():
            permission = Permission.objects.select_for_update().get(id=id)
            permission.name = name
            permission.display_name = data.get('display_name')
            # hanya update description jika diberikan
            if 'description' in data:
                permission.description = data.get('description')
            permission.save()

        return JsonResponse({'message': 'Permission berhasil diperbarui.'})
    except Permission.DoesNotExist:
        return JsonResponse({'message': 'Permission tidak ditemukan.'}, status=404)
    except IntegrityError:
        return JsonResponse({'message': 'Permission dengan nama ini sudah ada.'}, status=409)
    except Exception as e:
        return JsonResponse({'message': f'Error: {str(e)}'}, status=500)

# ✅ Delete permission
@require_http_methods(["DELETE"])
@login_required(login_url='')
@permission_required('delete_permission')
@log_action_fbv("Delete Permission", subject_type="Permission")
def delete_permission(request, id):
    try:
        permission = Permission.objects.get(id=id)
        permission.delete()
        return JsonResponse({'message': 'Permission berhasil dihapus.'})
    except Permission.DoesNotExist:
        return JsonResponse({'message': 'Permission tidak ditemukan.'}, status=404)
    except Exception as e:
        return JsonResponse({'message': f'Error: {str(e)}'}, status=500)

# ✅ List permission sebagai JSON
@login_required(login_url='')
@permission_required('view_permission')
def list_permission_json(request):
    if request.method == 'GET':
        try:
            permissions = Permission.objects.all().values('id', 'name', 'display_name', 'description')
            return JsonResponse(list(permissions), safe=False)
        except Exception as e:
            return JsonResponse({'message': f'Error: {str(e)}'}, status=500)
    return JsonResponse({'message': 'Method tidak diizinkan.'}, status=405)
