from rest_framework import serializers
from .models import Role, Permission, MenuPermission, RolePermission
from AdminMenu.models import Menu
from django.db import transaction

class PermissionSimpleSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()

    class Meta:
        model = Permission
        fields = ['id', 'name']

    def get_name(self, obj):
        return getattr(obj, 'name', None) or f'Permission {obj.id}'

class MenuWithPermissionsSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = Menu
        fields = ['id', 'name', 'permissions']

    def get_permissions(self, obj):
        ctx = self.context or {}
        menu_permissions_map = ctx.get('menu_permissions_map', {})
        perms = menu_permissions_map.get(obj.id, [])
        return PermissionSimpleSerializer(perms, many=True).data

class RoleSerializer(serializers.ModelSerializer):
    # permissions: sudah diprefetch via role queryset → aman
    permissions = PermissionSimpleSerializer(many=True, read_only=True)

    # tulis permissions via PK
    permission_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Permission.objects.all().only('id'),
        source='permissions',
        write_only=True
    )

    # tidak query, baca dari context: ctx['role_menus_map'][role_id] -> list Menu
    menus = serializers.SerializerMethodField()

    created_at = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S", read_only=True)
    updated_at = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S", read_only=True)

    class Meta:
        model = Role
        fields = [
            'id', 'name', 'display_name', 'description',
            'permissions', 'permission_ids', 'menus',
            'created_at', 'updated_at'
        ]

    def get_menus(self, obj):
        ctx = self.context or {}
        role_menus_map = ctx.get('role_menus_map', {})
        menus = role_menus_map.get(obj.id, [])
        # Perlu kirim map permission per-menu agar child serializer tidak query
        return MenuWithPermissionsSerializer(
            menus, many=True,
            context={
                **self.context,
                # forward map yang sama ke child
                'menu_permissions_map': ctx.get('menu_permissions_map', {})
            }
        ).data

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep['display_name'] = rep.get('display_name') or ''
        rep['description'] = rep.get('description') or ''
        rep['permissions'] = rep.get('permissions') or []
        rep['menus'] = rep.get('menus') or []
        rep['created_at'] = rep.get('created_at') or ''
        rep['updated_at'] = rep.get('updated_at') or ''
        return rep

    def create(self, validated_data):
        # optimized: bulk_create di through table
        permissions = validated_data.pop('permissions', [])
        validated_data.pop('id', None)
        with transaction.atomic():
            role = Role.objects.create(**validated_data)
            if permissions:
                RolePermission.objects.bulk_create([
                    RolePermission(role=role, permission=perm if isinstance(perm, Permission) else Permission(id=perm))
                    for perm in permissions
                ], ignore_conflicts=True)
        return role

    def update(self, instance, validated_data):
        permissions = validated_data.pop('permissions', None)
        validated_data.pop('id', None)
        validated_data.pop('created_at', None)
        validated_data.pop('updated_at', None)

        with transaction.atomic():
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()

            if permissions is not None:
                new_perm_ids = {
                    perm.id if isinstance(perm, Permission) else int(perm)
                    for perm in permissions
                }

                # permission ID yang sekarang ada di DB
                current_perm_ids = set(
                    RolePermission.objects
                    .filter(role=instance)
                    .values_list('permission_id', flat=True)
                )

                # ➖ hapus hanya yang tidak dicentang
                remove_ids = current_perm_ids - new_perm_ids
                if remove_ids:
                    RolePermission.objects.filter(
                        role=instance,
                        permission_id__in=remove_ids
                    ).delete()

                # ➕ tambah yang baru dicentang
                add_ids = new_perm_ids - current_perm_ids
                if add_ids:
                    RolePermission.objects.bulk_create(
                        [
                            RolePermission(
                                role=instance,
                                permission_id=pid
                            ) for pid in add_ids
                        ],
                        ignore_conflicts=True
                    )

        return instance