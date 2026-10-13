from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics
from django.db.models import Prefetch
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404
from rest_framework.permissions import IsAuthenticated, BasePermission
from rest_framework.decorators import permission_classes
from collections import defaultdict

from AdminRole.models import RolePermission
from AdminMenu.models import Menu
from .models import Role, Permission, MenuPermission
from .serializers import RoleSerializer
from AdminMenu.auth_helpers import permission_required, user_has_permission, class_permission_required
from common.const.role_map import has_role, ROLE_KEYS


def user_is_superadmin(user):
    if not user or not user.is_authenticated:
        return False
    role_names = []
    if getattr(user, 'roles', None) is not None:
        role_names = list(user.roles.values_list('name', flat=True))
    return has_role(role_names, ROLE_KEYS["SUPERADMIN"])


class IsSuperAdmin(BasePermission):
    def has_permission(self, request, view):
        return user_is_superadmin(request.user)

# ✅ View page untuk Role Admin
@login_required(login_url='')
@permission_required('view_role')
def view_role_admin(request):
    if not user_is_superadmin(request.user):
        return render(request, '403.html', status=403)
    return render(request, "content/page-users-role.html", {"user": request.user})

def build_context_maps(roles):
    """
    Build semua map yang dibutuhkan serializer tanpa N+1 query.
    """
    role_ids = [r.id for r in roles]
    if not role_ids:
        return {'menu_permissions_map': {}, 'role_menus_map': {}}

    # 1) Prefetch permissions milik role
    # Pastikan roles sudah punya ._prefetched_objects_cache['permissions']
    # Kalau belum, kita ambil cepat:
    role_perms = (RolePermission.objects
                  .filter(role_id__in=role_ids)
                  .values('role_id', 'permission_id'))

    # Kumpulkan permission_id per role
    role_perm_ids_map = defaultdict(set)
    all_perm_ids = set()
    for rp in role_perms:
        pid = rp['permission_id']
        rid = rp['role_id']
        role_perm_ids_map[rid].add(pid)
        all_perm_ids.add(pid)

    # 2) Ambil Permission object minimal (sekali)
    perm_objs = list(Permission.objects.filter(id__in=all_perm_ids).only('id', 'name'))
    perm_by_id = {p.id: p for p in perm_objs}

    # 3) Ambil semua MenuPermission untuk permission2 tersebut
    mp_rows = (MenuPermission.objects
               .filter(permission_id__in=all_perm_ids)
               .values('menu_id', 'permission_id'))

    # 4) map: menu_id -> set(permission_id)
    menu_perm_ids_map = defaultdict(set)
    for row in mp_rows:
        menu_perm_ids_map[row['menu_id']].add(row['permission_id'])

    # 5) Ambil Menu object minimal untuk semua menu yang terlibat
    all_menu_ids = set(menu_perm_ids_map.keys())
    menu_objs = list(Menu.objects.filter(id__in=all_menu_ids).only('id', 'name'))
    menu_by_id = {m.id: m for m in menu_objs}

    # 6) menu_permissions_map: menu_id -> list Permission objs (untuk child serializer)
    menu_permissions_map = {
        mid: [perm_by_id[pid] for pid in sorted(menu_perm_ids_map[mid])]
        for mid in all_menu_ids
    }

    # 7) role_menus_map: role_id -> list Menu objs (menu yang punya permission intersect dengan role)
    role_menus_map = {}
    for rid, perm_ids in role_perm_ids_map.items():
        # union semua menu yang memiliki permission2 tsb
        menu_ids = {mid for mid, pids in menu_perm_ids_map.items() if pids & perm_ids}
        # optional: sort by id untuk stabil
        role_menus_map[rid] = [menu_by_id[mid] for mid in sorted(menu_ids)]

    return {
        'menu_permissions_map': menu_permissions_map,
        'role_menus_map': role_menus_map,
    }

# ✅ Role List & Create
class RoleListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = RoleSerializer

    def get_queryset(self):
        # kolom minimal + prefetch permission minimal
        return (Role.objects
                .only('id', 'name', 'display_name', 'description', 'created_at', 'updated_at')
                .prefetch_related(
                    Prefetch('permissions', queryset=Permission.objects.only('id', 'name'))
                )
                .order_by('id'))

    def list(self, request, *args, **kwargs):
        roles = list(self.get_queryset())
        ctx_maps = build_context_maps(roles)
        serializer = self.get_serializer(roles, many=True, context={**self.get_serializer_context(), **ctx_maps})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = serializer.save()

        # refresh untuk representasi lengkap
        role = (Role.objects
                .only('id', 'name', 'display_name', 'description', 'created_at', 'updated_at')
                .prefetch_related(Prefetch('permissions', queryset=Permission.objects.only('id', 'name')))
                .get(pk=role.pk))

        ctx_maps = build_context_maps([role])
        data = self.get_serializer(role, context={**self.get_serializer_context(), **ctx_maps}).data
        return Response({'message': 'Role berhasil ditambahkan.', 'data': data}, status=status.HTTP_201_CREATED)

# ✅ Role Detail (GET, PUT, DELETE)
class RoleDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = RoleSerializer

    def get_queryset(self):
        return (Role.objects.only('id', 'name', 'display_name', 'description', 'created_at', 'updated_at').prefetch_related(Prefetch('permissions', queryset=Permission.objects.only('id', 'name'))))

    def retrieve(self, request, *args, **kwargs):
        role = self.get_object()
        ctx_maps = build_context_maps([role])
        serializer = self.get_serializer(role, context={**self.get_serializer_context(), **ctx_maps})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def update(self, request, *args, **kwargs):
        role = self.get_object()
        serializer = self.get_serializer(role, data=request.data)
        serializer.is_valid(raise_exception=True)
        role = serializer.save()

        # refresh + context map
        role = self.get_queryset().get(pk=role.pk)
        ctx_maps = build_context_maps([role])
        data = self.get_serializer(role, context={**self.get_serializer_context(), **ctx_maps}).data
        return Response({'message': 'Role berhasil diperbarui.', 'data': data}, status=status.HTTP_200_OK)
    
    def destroy(self, request, *args, **kwargs):
        role = self.get_object()
        role.delete()
        return Response(
            {'message': 'Role berhasil dihapus.'},
            status=status.HTTP_200_OK
        )

# ✅ Role-Menu-Permission View
@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(permission_classes([IsAuthenticated]), name='dispatch')
class RoleMenuPermissionView(APIView):

    @class_permission_required('view_role')
    def get(self, request, role_id, menu_id):
        if not user_is_superadmin(request.user):
            return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        try:
            menu = get_object_or_404(Menu, pk=menu_id)
            menu_permission_ids = MenuPermission.objects.filter(
                menu=menu
            ).values_list('permission_id', flat=True)

            permissions = Permission.objects.filter(id__in=menu_permission_ids).order_by('id')
            result = []

            if int(role_id) != 0:
                role = get_object_or_404(Role, pk=role_id)
                role_permission_ids = set(role.permissions.values_list('id', flat=True))
                for perm in permissions:
                    result.append({
                        'id': perm.id,
                        'name': perm.name,
                        'is_checked': perm.id in role_permission_ids
                    })
            else:
                for perm in permissions:
                    result.append({
                        'id': perm.id,
                        'name': perm.name,
                        'is_checked': False
                    })
            return Response(result, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
