from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from .models import Notary
from .serializers import NotarySerializer
from django.core.serializers.json import DjangoJSONEncoder
from django.core.exceptions import ValidationError
from django.db.models import Q
from AdminMenu.auth_helpers import get_user_role_names, user_has_permission
from django.views.decorators.cache import never_cache

# Menampilkan halaman notaris
@login_required(login_url='')
def view_notaris_users_admin(request):
    if not user_has_permission(request.user, "read-notaris"):
        return render(request, "403.html", status=403)
    return render(request, "content/page-users-notaris.html", {"user": request.user})

# Create Notary (Menambahkan Notaris)
@login_required(login_url='')
def create_notary(request):
    if not user_has_permission(request.user, "create-notaris"):
        return JsonResponse({'status': 'error', 'message': 'Forbidden'}, status=403)
    if request.method == 'POST':
        try:
            data = request.POST
            notary = Notary(
                user=request.user,  # ✅ user dari session login
                firm_name=data.get('firm_name'),
                full_name=data.get('full_name'),
                short_title=data.get('short_title'),
                full_title=data.get('full_title'),
                email=data.get('email'),
                phone_number=data.get('phone_number'),
                firm_address=data.get('firm_address'),
                city_name=data.get('city_name'),
                website=data.get('website'),
                status=data.get('status'),
                is_active=data.get('is_active') == 'on',
            )
            notary.full_clean()
            notary.save()
            return JsonResponse({'status': 'success', 'message': 'Notaris berhasil ditambahkan.'})
        except ValidationError as ve:
            return JsonResponse({'status': 'error', 'message': str(ve)})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})

    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'})

# Edit Notary (Mengedit Notaris)
@login_required(login_url='')
def edit_notary(request, id):
    # Cek apakah notaris ada di database
    notary = get_object_or_404(Notary, id=id)

    if request.method == 'GET':
        if not user_has_permission(request.user, "read-notaris"):
            return JsonResponse({'status': 'error', 'message': 'Forbidden'}, status=403)
        # Menyediakan data notaris dalam format JSON untuk digunakan di frontend (modal edit)
        serializer = NotarySerializer(notary)
        return JsonResponse(serializer.data)  # Kirimkan data notaris ke frontend

    elif request.method == 'POST':
        # Mengupdate notaris dengan data baru dari form
        if not user_has_permission(request.user, "update-notaris"):
            return JsonResponse({'status': 'error', 'message': 'Forbidden'}, status=403)
        try:
            data = request.POST
            notary.firm_name = data.get('firm_name')
            notary.full_name = data.get('full_name')
            notary.short_title = data.get('short_title')
            notary.full_title = data.get('full_title')
            notary.email = data.get('email')
            notary.phone_number = data.get('phone_number')
            notary.firm_address = data.get('firm_address')
            notary.city_name = data.get('city_name')
            notary.website = data.get('website')
            notary.status = data.get('status')
            notary.is_active = data.get('is_active') == 'on'
            notary.full_clean()  # Validasi model
            notary.save()  # Simpan perubahan
            return JsonResponse({'status': 'success', 'message': 'Notaris berhasil diperbarui.'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})

    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'})

# Delete Notary (Menghapus Notaris)
@login_required(login_url='')
def delete_notary(request, id):
    if not user_has_permission(request.user, "delete-notaris"):
        return JsonResponse({'status': 'error', 'message': 'Forbidden'}, status=403)
    notary = get_object_or_404(Notary, id=id)
    try:
        notary.delete()  # Hapus notaris dari database
        return JsonResponse({'status': 'success', 'message': 'Notaris berhasil dihapus.'})  # Respon JSON sukses
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})  # Respon JSON error

@never_cache
@login_required(login_url='')
def get_notary_data(request):
    if not (
        user_has_permission(request.user, "read-notaris") or
        user_has_permission(request.user, "read-nama-notaris")
    ):
        return JsonResponse({'status': 'error', 'message': 'Forbidden'}, status=403)
    columns = [
        'firm_name', 'full_name', 'short_title', 'full_title',
        'email', 'phone_number', 'firm_address', 'city_name',
        'website', 'register_date', 'status'
    ]

    qs = Notary.objects.all()

    # === FIX: normalisasi role jadi lowercase ===
    try:
        raw_roles = list(get_user_role_names(request.user))
    except Exception:
        raw_roles = []
    roles_lc = {str(r).lower() for r in raw_roles}

    # Hanya batasi kalau user punya role 'notaris' (case-insensitive)
    if 'notaris' in roles_lc:
        # Kalau ada FK ke user → paling akurat
        if hasattr(Notary, 'user_id'):
            qs = qs.filter(user=request.user)
        else:
            # Fallback lewat identitas user
            qs = qs.filter(
                Q(email__iexact=getattr(request.user, 'email', '')) |
                Q(full_name__iexact=getattr(request.user, 'name', ''))
            )

    # Filter opsional dari querystring
    firm_name = (request.GET.get('firm_name') or '').strip()
    if firm_name:
        qs = qs.filter(firm_name__iexact=firm_name)

    # Deteksi mode DataTables
    draw  = request.GET.get('draw')
    start = request.GET.get('start')
    length = request.GET.get('length')

    # === Mode non-DataTables → untuk dropdown ===
    if draw is None or start is None or length is None:
        notaries = qs.order_by('firm_name')
        data = [{
            "id": n.id,
            "firm_name": n.firm_name,
            "full_name": n.full_name,
            "short_title": n.short_title,
            "full_title": n.full_title,
            "email": n.email,
            "phone_number": n.phone_number,
            "firm_address": n.firm_address,
            "city_name": n.city_name,
            "website": n.website,
            "register_date": n.register_date.strftime("%Y-%m-%d") if getattr(n, 'register_date', None) else "",
            "status": n.status,
        } for n in notaries]
        return JsonResponse(data, safe=False)

    # === Mode DataTables ===
    try:
        start_i  = int(start)
        length_i = int(length)
    except (TypeError, ValueError):
        start_i, length_i = 0, 10

    records_total = qs.count()

    search_value = (request.GET.get('search[value]') or '').strip()
    if search_value:
        qs = qs.filter(
            Q(firm_name__icontains=search_value) |
            Q(full_name__icontains=search_value) |
            Q(short_title__icontains=search_value) |
            Q(full_title__icontains=search_value) |
            Q(email__icontains=search_value) |
            Q(phone_number__icontains=search_value) |
            Q(firm_address__icontains=search_value) |
            Q(city_name__icontains=search_value) |
            Q(website__icontains=search_value) |
            Q(status__icontains=search_value)
        )

    records_filtered = qs.count()

    order_col_index = request.GET.get('order[0][column]', '0')
    order_dir       = request.GET.get('order[0][dir]', 'asc')
    try:
        order_col_index = int(order_col_index)
        order_field = columns[order_col_index]
    except (ValueError, IndexError):
        order_field = 'id'
    if order_dir == 'desc':
        order_field = '-' + order_field
    qs = qs.order_by(order_field)

    page = qs[start_i:start_i + length_i]

    data = [{
        "id": n.id,
        "firm_name": n.firm_name,
        "full_name": n.full_name,
        "short_title": n.short_title,
        "full_title": n.full_title,
        "email": n.email,
        "phone_number": n.phone_number,
        "firm_address": n.firm_address,
        "city_name": n.city_name,
        "website": n.website,
        "register_date": n.register_date.strftime("%Y-%m-%d") if getattr(n, 'register_date', None) else "",
        "status": n.status,
    } for n in page]

    return JsonResponse({
        "draw": int(draw),
        "recordsTotal": records_total,
        "recordsFiltered": records_filtered,
        "data": data,
    }, safe=False)

