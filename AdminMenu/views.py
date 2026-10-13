from django.shortcuts import get_object_or_404, render
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics, permissions
from rest_framework.authentication import SessionAuthentication, BasicAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import api_view, permission_classes

from AdminPermission.models import Permission
from .models import Menu, Root
from .serializers import MenuSerializer, PermissionSerializer, ProvinceOptionSerializer, NotaryOptionSerializer, WitnessSerializer

from django.views.decorators.cache import cache_page
from django.db.models import Prefetch, Q
from collections import defaultdict

from AdminUser.models import Region
from AdminNotaries.models import Notary
from assignakta.models import Witness

from .auth_helpers import (
    get_user_menus,
    permission_required,
)

from django.db import transaction
from AdminMenu.auth_helpers import permission_required, user_has_permission, log_action_fbv, log_action_cbv

# ------------------------
# 🌐 Admin Page View
# ------------------------

@login_required(login_url='')
@permission_required('read-menu')
def view_menu_admin(request):
    menus = (
        Menu.objects
        .filter(parent__isnull=True)
        .prefetch_related('permissions', 'children__permissions', 'children__children')
        .select_related('menu_root')
    )

    permissions = Permission.objects.all().order_by('name')
    roots = Root.objects.all().order_by('root_name')  # ✅ perbaiki disini
    permissions_data = list(permissions.values('id', 'display_name'))

    context = {
        'menus': menus,
        'permissions': permissions,
        'permissions_data': permissions_data,
        'root': roots
    }

    return render(request, 'content/page-users-menu.html', context)

# 📌 API: Sidebar Menu
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_sidebar_menu(request):
    try:
        user = request.user
        if user.is_superuser or user.username.lower() == "admin":
            menus = Menu.objects.select_related('parent') \
                                .prefetch_related('permissions', 'children') \
                                .all().order_by("created_at")
        else:
            menus = get_user_menus(user).order_by("created_at")

        serializer = MenuSerializer(menus, many=True, context={'depth': 4, 'current_depth': 1})
        return Response(serializer.data)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({"error": str(e)}, status=500)

# 📥 API: Menu List & Create
@log_action_cbv("Create Menu",subject_type="Menu",methods=("POST",),subject_id_getter=lambda self, request, *args, **kwargs: getattr(request, "_last_created_menu_id", None))
@method_decorator(csrf_exempt, name='dispatch')
class MenuListCreateView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(cache_page(60 * 5))             # cache 5 menit (opsional)
    @method_decorator(permission_required('read-menu'))
    def get(self, request):
        perm_qs = Permission.objects.only("id", "display_name")
        qs = (Menu.objects
              .select_related("parent", "menu_root")
              .only("id","name","link","icon_css","icon_html","level",
                    "parent_id","menu_root_id","created_at","updated_at")
              .prefetch_related(Prefetch("permissions", queryset=perm_qs))
        )

        menus = list(qs)

        children_map = defaultdict(list)
        roots = []
        for m in menus:
            if m.parent_id:
                children_map[m.parent_id].append(m)
            else:
                roots.append(m)

        serializer = MenuSerializer(
            roots, many=True,
            context={'depth': int(request.GET.get('depth', 4) or 4),
                     'current_depth': 1,
                     'children_map': children_map}
        )
        return Response(serializer.data)

    @method_decorator(permission_required('create-menu'))
    def post(self, request):
        with transaction.atomic():
            serializer = MenuSerializer(data=request.data, context={'request': request})
            serializer.is_valid(raise_exception=True)
            instance = serializer.save()
            # >>> penting untuk logging ID hasil create:
            request._last_created_menu_id = instance.id
        data = MenuSerializer(instance, context={'depth': 4, 'current_depth': 1}).data
        return Response(data, status=201)
    
@log_action_cbv("Update Menu", subject_type="Menu", methods=("PUT","PATCH"))
@log_action_cbv("Delete Menu", subject_type="Menu", methods=("DELETE",))
@method_decorator(csrf_exempt, name='dispatch')
class MenuDetailView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        return get_object_or_404(Menu, pk=pk)

    @method_decorator(permission_required('read-menu'))  # ✅ alias → read-menu
    def get(self, request, pk):
        menu = self.get_object(pk)
        serializer = MenuSerializer(menu, context={'depth': 3, 'current_depth': 1})
        return Response(serializer.data)

    @method_decorator(permission_required('update-menu'))
    def put(self, request, pk):
        menu = self.get_object(pk)
        with transaction.atomic():
            serializer = MenuSerializer(menu, data=request.data, context={'request': request})
            serializer.is_valid(raise_exception=True)
            instance = serializer.save()
        return Response(MenuSerializer(instance, context={'depth': 3, 'current_depth': 1}).data)

    @method_decorator(permission_required('delete-menu'))
    def delete(self, request, pk):
        with transaction.atomic():
            menu = self.get_object(pk)
            menu.delete()
        return Response({'message': 'Menu berhasil dihapus.'}, status=status.HTTP_204_NO_CONTENT)

@method_decorator(csrf_exempt, name='dispatch')
class PermissionDetailView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        return get_object_or_404(Permission, pk=pk)

    @method_decorator(permission_required('read-permission'))
    def get(self, request, pk):
        permission = self.get_object(pk)
        serializer = PermissionSerializer(permission)
        return Response(serializer.data)

# 👤 Admin Pages for Specific Roles
@login_required(login_url='')
def view_notaris_users_admin(request):
    if not user_has_permission(request.user, "read-notaris"):
        return render(request, "403.html", status=403)
    return render(request, "content/page-users-notaris.html", {"user": request.user})

@login_required(login_url='')
def view_pnrmfds_users_admin(request):
    if not user_has_permission(request.user, "read-penerima"):
        return render(request, "403.html", status=403)
    return render(request, "content/page-users-fidusia.html", {"user": request.user})

@login_required(login_url='')
def view_pnrmks_users_admin(request):
    if not user_has_permission(request.user, "read-penerima_kuasa"):
        return render(request, "403.html", status=403)
    return render(request, "content/page-users-kuasa.html", {"user": request.user})

@login_required(login_url='')
def view_saksi_users_admin(request):
    if not user_has_permission(request.user, "read-saksi"):
        return render(request, "403.html", status=403)
    return render(request, "content/page-users-saksi.html", {"user": request.user})

@log_action_cbv("Witness CRUD", subject_type="Witness", methods=("POST",))
class WitnessListCreateView(generics.ListCreateAPIView):
    queryset = Witness.objects.select_related("province", "notary").order_by("-id")
    serializer_class = WitnessSerializer
    permission_classes = [permissions.IsAuthenticated]

    @method_decorator(permission_required('read-saksi'))
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @method_decorator(permission_required('create-saksi'))
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)
@log_action_cbv("Witness CRUD", subject_type="Witness", methods=("PUT","PATCH","DELETE"))
class WitnessRetrieveUpdateDeleteView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Witness.objects.select_related("province", "notary")
    serializer_class = WitnessSerializer
    permission_classes = [permissions.IsAuthenticated]

    @method_decorator(permission_required('read-saksi'))
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @method_decorator(permission_required('update-saksi'))
    def put(self, request, *args, **kwargs):
        return super().put(request, *args, **kwargs)

    @method_decorator(permission_required('update-saksi'))
    def patch(self, request, *args, **kwargs):
        return super().patch(request, *args, **kwargs)

    @method_decorator(permission_required('delete-saksi'))
    def delete(self, request, *args, **kwargs):
        return super().delete(request, *args, **kwargs)

class ProvinceOptionsView(generics.ListAPIView):
    queryset = Region.objects.filter(region_level='province').order_by("region_name")
    serializer_class = ProvinceOptionSerializer
    permission_classes = [permissions.IsAuthenticated]

    @method_decorator(permission_required('read-saksi'))
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

class NotaryOptionsView(generics.ListAPIView):
    queryset = Notary.objects.order_by("full_name")
    serializer_class = NotaryOptionSerializer
    permission_classes = [permissions.IsAuthenticated]

    @method_decorator(permission_required('read-saksi'))
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)
    
class WitnessDataTableView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    ORDERABLE_MAP = {
        "full_name": "full_name",
        "short_title": "short_title",
        "full_title": "full_title",
        "notary_name": "notary__full_name",
        "birth_info": "birth_info",
        "nationality": "nationality",
        "residence": "residence",
        "address": "address",
        "rt": "rt",
        "rw": "rw",
        "subdistrict": "subdistrict",
        "district": "district",
        "province_name": "province__region_name",
        "national_id": "national_id",
        "created_at": "created_at",
        "updated_at": "updated_at",
    }

    @method_decorator(permission_required('read-saksi'))
    def get(self, request):
        draw   = int(request.GET.get("draw", "1"))
        start  = int(request.GET.get("start", 0))
        length = int(request.GET.get("length", 10))
        search_value = request.GET.get("search[value]", "").strip()

        qs_base = Witness.objects.select_related("province", "notary")
        records_total = qs_base.count()

        # filter (global search)
        if search_value:
            q = (
                Q(full_name__icontains=search_value) |
                Q(short_title__icontains=search_value) |
                Q(full_title__icontains=search_value) |
                Q(notary__full_name__icontains=search_value) |
                Q(birth_info__icontains=search_value) |
                Q(nationality__icontains=search_value) |
                Q(residence__icontains=search_value) |
                Q(address__icontains=search_value) |
                Q(rt__icontains=search_value) |
                Q(rw__icontains=search_value) |
                Q(subdistrict__icontains=search_value) |
                Q(district__icontains=search_value) |
                Q(province__region_name__icontains=search_value) |
                Q(national_id__icontains=search_value)
            )
            qs = qs_base.filter(q)
        else:
            qs = qs_base

        records_filtered = qs.count()

        # ordering
        order_col_idx = request.GET.get("order[0][column]")
        order_dir     = request.GET.get("order[0][dir]", "asc")
        if order_col_idx is not None:
            col_name = request.GET.get(f"columns[{order_col_idx}][data]", "")
            mapped   = self.ORDERABLE_MAP.get(col_name)
            if mapped:
                if order_dir == "desc":
                    mapped = f"-{mapped}"
                qs = qs.order_by(mapped)
            else:
                qs = qs.order_by("-id")
        else:
            qs = qs.order_by("-id")

        # paging
        if length != -1:
            qs = qs[start:start+length]

        # bentuk data sesuai kolom DataTables
        data = []
        for w in qs:
            data.append({
                "id": w.id,
                "full_name": w.full_name or "",
                "short_title": w.short_title or "",
                "full_title": w.full_title or "",
                "notary_name": w.notary.full_name if w.notary else "",
                "birth_info": w.birth_info or "",
                "nationality": w.nationality or "",
                "residence": w.residence or "",
                "address": w.address or "",
                "rt": w.rt or "",
                "rw": w.rw or "",
                "subdistrict": w.subdistrict or "",
                "district": w.district or "",
                "province_name": w.province.region_name if w.province else "",
                "national_id": w.national_id or "",
            })

        return Response({
            "draw": draw,
            "recordsTotal": records_total,
            "recordsFiltered": records_filtered,
            "data": data,
        })
        
@login_required(login_url='')
def faq(request):
    return render(request, "content/faq.html")

@login_required(login_url='')
def contact(request):
    return render(request, "content/contact.html")
