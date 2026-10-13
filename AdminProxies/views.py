from django.db import transaction, IntegrityError, DataError
from django.http import JsonResponse
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.shortcuts import get_object_or_404, render
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from AdminMenu.auth_helpers import user_has_permission

# --- helper: map alias UI -> field model dan rapikan nilai ---
def _normalize_payload(data: dict, instance=None) -> dict:
    d = data.copy()
    d.pop('csrfmiddlewaretoken', None)

    # alias UI -> field model
    if d.get('short_degree') and not d.get('short_title'):
        d['short_title'] = d['short_degree']
    if d.get('long_degree') and not d.get('full_title'):
        d['full_title'] = d['long_degree']
    if d.get('ktp_number') and not d.get('national_id'):
        d['national_id'] = d['ktp_number']
    if d.get('city') and not d.get('residence'):
        d['residence'] = d['city']
    if d.get('province') and not d.get('province_id'):
        d['province_id'] = d['province']  # pastikan ini ID yang valid

    # gabung birth_place + birth_date -> birth_info
    birth_place = d.pop('birth_place', None)
    birth_date  = d.pop('birth_date', None)
    if birth_place or birth_date:
        if birth_place and birth_date:
            d['birth_info'] = f"{birth_place}, {birth_date}"
        elif birth_place and instance and getattr(instance, 'birth_info', None):
            parts = [p.strip() for p in str(instance.birth_info).split(',')]
            old_date = parts[1] if len(parts) > 1 else ''
            d['birth_info'] = f"{birth_place}{', ' + old_date if old_date else ''}"
        elif birth_date and instance and getattr(instance, 'birth_info', None):
            parts = [p.strip() for p in str(instance.birth_info).split(',')]
            old_place = parts[0] if parts else ''
            d['birth_info'] = f"{old_place}{', ' + birth_date if birth_date else ''}"
        else:
            d['birth_info'] = birth_place or birth_date

    return d

# View halaman admin (tetap)
@login_required(login_url='')
def view_pnrmks_users_admin(request):
    if not user_has_permission(request.user, "read-penerima_kuasa"):
        return render(request, "403.html", status=403)
    user = request.user
    return render(request, "content/page-users-kuasa.html", {"user": user})

# Create AdminProxy (ditingkatkan)
@api_view(['POST'])
def create_proxies(request):
    if not user_has_permission(request.user, "create-penerima_kuasa"):
        return Response({'detail': 'Forbidden'}, status=403)
    from .serializers import AdminProxySerializer
    from .models import AdminProxy

    payload = _normalize_payload(request.data)

    # (Opsional) pre-check unik biar tidak IntegrityError:
    nat = payload.get('national_id')
    if nat and AdminProxy.objects.filter(national_id=nat).exists():
        return JsonResponse(
            {'status': 'error', 'errors': {'national_id': ['Sudah terdaftar.']}},
            status=400
        )

    serializer = AdminProxySerializer(data=payload)
    if not serializer.is_valid():
        return JsonResponse({'status': 'error', 'errors': serializer.errors}, status=400)

    try:
        with transaction.atomic():
            serializer.save()
        return JsonResponse({'status': 'success', 'message': 'Admin Proxy berhasil ditambahkan!'})
    except IntegrityError as e:
        # contoh penyebab: FK province_id/notary_id tidak valid, kolom NOT NULL kosong, UNIQUE duplikat
        return JsonResponse(
            {'status': 'error', 'errors': {'database': [str(e)]}},
            status=400
        )
    except DataError as e:
        # contoh: panjang kolom melebihi batas, tipe data tak cocok
        return JsonResponse(
            {'status': 'error', 'errors': {'database': [str(e)]}},
            status=400
        )

# Read / List AdminProxy — server-side DataTables (punyamu sudah oke)
@api_view(['GET'])
def get_proxies_data(request):
    if not user_has_permission(request.user, "read-penerima_kuasa"):
        return Response({'detail': 'Forbidden'}, status=403)
    from .serializers import AdminProxySerializer
    from .models import AdminProxy

    draw = int(request.GET.get('draw', '1') or 1)
    start = int(request.GET.get('start', '0') or 0)
    length = int(request.GET.get('length', '10') or 10)
    search_value = (request.GET.get('search[value]', '') or '').strip()

    columns = [
        'full_name','short_degree','long_degree','birth_place','birth_date','nationality','address',
        'rt','rw','subdistrict','district','city','province','ktp_number','aksi'
    ]
    order_col_idx = request.GET.get('order[0][column]')
    order_dir = request.GET.get('order[0][dir]', 'asc')

    qs = AdminProxy.objects.all()
    if search_value:
        qs = qs.filter(
            Q(full_name__icontains=search_value) |
            Q(short_title__icontains=search_value) |
            Q(full_title__icontains=search_value) |
            Q(address__icontains=search_value) |
            Q(subdistrict__icontains=search_value) |
            Q(district__icontains=search_value) |
            Q(residence__icontains=search_value) |
            Q(national_id__icontains=search_value)
        )

    records_total = AdminProxy.objects.count()
    records_filtered = qs.count()

    order_field = 'full_name'
    if order_col_idx is not None:
        try:
            idx = int(order_col_idx)
            col = columns[idx]
            order_map = {
                'full_name':'full_name','short_degree':'short_title','long_degree':'full_title',
                'birth_place':'birth_info','birth_date':'birth_info','nationality':'nationality',
                'address':'address','rt':'rt','rw':'rw','subdistrict':'subdistrict','district':'district',
                'city':'residence','province':'province_id','ktp_number':'national_id',
            }
            order_field = order_map.get(col, 'full_name')
            if order_dir == 'desc':
                order_field = f'-{order_field}'
        except Exception:
            pass

    qs = qs.order_by(order_field)
    qs_page = qs if length == -1 else qs[start:start+length]
    serializer = AdminProxySerializer(qs_page, many=True)

    return Response({
        'draw': draw,
        'recordsTotal': records_total,
        'recordsFiltered': records_filtered,
        'data': serializer.data
    })

# Edit & View (tetap + normalisasi payload)
@api_view(['GET', 'POST'])
def edit_proxies(request, id):
    from .serializers import AdminProxySerializer
    from .models import AdminProxy

    proxy = get_object_or_404(AdminProxy, id=id)

    if request.method == 'GET':
        if not user_has_permission(request.user, "read-penerima_kuasa"):
            return Response({'detail': 'Forbidden'}, status=403)
        serializer = AdminProxySerializer(proxy)
        return Response(serializer.data)

    if not user_has_permission(request.user, "update-penerima_kuasa"):
        return Response({'detail': 'Forbidden'}, status=403)
    payload = _normalize_payload(request.data, instance=proxy)
    serializer = AdminProxySerializer(proxy, data=payload, partial=True)
    if not serializer.is_valid():
        return JsonResponse({'status': 'error', 'errors': serializer.errors}, status=400)

    try:
        with transaction.atomic():
            serializer.save()
        return JsonResponse({'status': 'success', 'message': 'Data Admin Proxy berhasil diperbarui!'})
    except IntegrityError as e:
        return JsonResponse({'status': 'error', 'errors': {'database': [str(e)]}}, status=400)
    except DataError as e:
        return JsonResponse({'status': 'error', 'errors': {'database': [str(e)]}}, status=400)

# Delete (tetap)
@api_view(['POST'])
def delete_proxies(request, id):
    if not user_has_permission(request.user, "delete-penerima_kuasa"):
        return Response({'detail': 'Forbidden'}, status=403)
    from .models import AdminProxy
    proxy = get_object_or_404(AdminProxy, id=id)
    proxy.delete()
    return JsonResponse({'status': 'success', 'message': 'Admin Proxy berhasil dihapus!'})
