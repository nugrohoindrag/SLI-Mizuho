from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.db import transaction
from .models import Recipient
from .serializers import RecipientSerializer
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Q
from django.contrib.auth.decorators import login_required
from AdminMenu.auth_helpers import user_has_permission
import logging
logger = logging.getLogger(__name__)

# View untuk menampilkan halaman admin Penerima Fidusia
@login_required(login_url="")
def view_pnrmfds_users_admin(request):
    if not user_has_permission(request.user, "read-penerima"):
        return render(request, "403.html", status=403)
    return render(request, 'content/page-users-fidusia.html')

# CREATE
@api_view(['POST'])
def create_recipient(request):
    if not user_has_permission(request.user, "create-penerima"):
        return Response({'detail': 'Forbidden'}, status=403)
    try:
        serializer = RecipientSerializer(data=request.data)
        if serializer.is_valid():
            with transaction.atomic():
                serializer.save()
            return JsonResponse({'status': 'success', 'message': 'Penerima Fidusia berhasil ditambahkan!'})
        return Response({'status': 'error', 'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.exception("Create recipient failed")  # akan log traceback ke console/log
        return Response({'status': 'error', 'message': str(e)}, status=status.HTTP_400_BAD_REQUEST)

# READ (full)
@api_view(['GET'])
def get_recipient_data(request):
    if not user_has_permission(request.user, "read-penerima"):
        return Response({'detail': 'Forbidden'}, status=403)
    qs = Recipient.objects.all().only(
        'id','business_entity_name','email','phone_number','address','rt','rw',
        'subdistrict','district','city','province','postal_code',
        'decree_number','decree_date','decree_date_end','tax_number','ahu_decree_number'
    )
    serializer = RecipientSerializer(qs, many=True)
    return Response(serializer.data)

# DataTables server-side
@api_view(['GET'])
def get_recipient_datatable(request):
    if not user_has_permission(request.user, "read-penerima"):
        return JsonResponse({'detail': 'Forbidden'}, status=403)
    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = (request.GET.get('search[value]', '') or '').strip()

    # NOTE: urutan ini HARUS match urutan kolom DataTables di front-end
    orderable_columns = [
        'business_entity_name','email','phone_number','address','rt','rw',
        'subdistrict','district','city','province','postal_code',
        'decree_number','decree_date','decree_date_end','tax_number','ahu_decree_number',
    ]

    queryset = Recipient.objects.all()

    records_total = queryset.count()

    if search_value:
        q = Q()
        for field in [
            'business_entity_name__icontains','email__icontains','phone_number__icontains',
            'address__icontains','subdistrict__icontains','district__icontains',
            'city__icontains','province__icontains','decree_number__icontains',
            'tax_number__icontains','ahu_decree_number__icontains',
        ]:
            q |= Q(**{field: search_value})
        queryset = queryset.filter(q)

    records_filtered = queryset.count()

    # ordering
    order_col_index = request.GET.get('order[0][column]')
    order_dir = request.GET.get('order[0][dir]', 'asc')
    if order_col_index is not None:
        try:
            idx = int(order_col_index)
            # kalau kolom pertama di tabelmu adalah "Action" (tidak bisa di-sort),
            # pastikan index yang dikirim DT sudah disesuaikan.
            if 0 <= idx < len(orderable_columns):
                field = orderable_columns[idx]
                if order_dir == 'desc':
                    field = f'-{field}'
                queryset = queryset.order_by(field)
        except ValueError:
            pass

    # pagination
    page = queryset[start:start+length]

    data = list(page.values(
        'id','business_entity_name','email','phone_number','address','rt','rw',
        'subdistrict','district','city','province','postal_code',
        'decree_number','decree_date','decree_date_end','tax_number','ahu_decree_number'
    ))

    return JsonResponse({
        'draw': draw,
        'recordsTotal': records_total,
        'recordsFiltered': records_filtered,
        'data': data
    })

# EDIT / VIEW detail
@api_view(['GET', 'PUT', 'PATCH', 'POST'])
def edit_recipient(request, id):
    recipient = get_object_or_404(Recipient, id=id)

    if request.method == 'GET':
        if not user_has_permission(request.user, "read-penerima"):
            return Response({'detail': 'Forbidden'}, status=403)
        serializer = RecipientSerializer(recipient)
        return Response(serializer.data)

    partial = (request.method == 'PATCH')
    if not user_has_permission(request.user, "update-penerima"):
        return Response({'detail': 'Forbidden'}, status=403)
    serializer = RecipientSerializer(recipient, data=request.data, partial=partial)
    if serializer.is_valid():
        serializer.save()
        return JsonResponse({'status': 'success', 'message': 'Data Penerima Fidusia berhasil diperbarui!'})
    return Response({'status': 'error', 'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)


# DELETE
@api_view(['DELETE', 'POST'])
def delete_recipient(request, id):
    if not user_has_permission(request.user, "delete-penerima"):
        return Response({'detail': 'Forbidden'}, status=403)
    recipient = get_object_or_404(Recipient, id=id)
    recipient.delete()
    return JsonResponse({'status': 'success', 'message': 'Penerima Fidusia berhasil dihapus!'})
