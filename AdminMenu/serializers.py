from .models import Menu, Root
from AdminPermission.models import Permission
from AdminUser.models import Region
from AdminNotaries.models import Notary
from assignakta.models import Witness
from django.db.models import Q
from rest_framework.views import APIView
from rest_framework import permissions, serializers
from rest_framework.response import Response

class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ['id', 'display_name']

class MenuSerializer(serializers.ModelSerializer):
    permissions = PermissionSerializer(read_only=True, many=True)
    menu_root_id_read = serializers.SerializerMethodField()

    permission_ids = serializers.PrimaryKeyRelatedField(
        queryset=Permission.objects.all(),
        source='permissions',
        many=True,
        write_only=True,
        required=False
    )

    parent = serializers.StringRelatedField(read_only=True)
    parent_id = serializers.SerializerMethodField()
    parent_id_write = serializers.PrimaryKeyRelatedField(
        queryset=Menu.objects.all(),
        source='parent',
        write_only=True,
        required=False,
        allow_null=True
    )
    parent_id_read = serializers.SerializerMethodField()
    menu_root = serializers.StringRelatedField(read_only=True)
    menu_root_id = serializers.PrimaryKeyRelatedField(
        queryset=Root.objects.all(),
        source='menu_root',
        write_only=True,
        required=False,
        allow_null=True
    )

    children = serializers.SerializerMethodField()

    class Meta:
        model = Menu
        fields = [
            'id', 'name', 'link', 'icon_css', 'icon_html', 'level',
            'parent', 'parent_id', 'parent_id_write', 'parent_id_read',
            'menu_root', 'menu_root_id', 'menu_root_id_read',  # ✅ tambahan
            'permissions', 'permission_ids',
            'children', 'created_at', 'updated_at',
        ]

        read_only_fields = [
            'id', 'permissions', 'parent',
            'parent_id', 'parent_id_read', 'children',
            'menu_root', 'menu_root_id_read',  # ✅ tambahan
            'created_at', 'updated_at',
        ]

    def get_parent_id(self, obj):
        return obj.parent.id if obj.parent else None
    
    def get_menu_root_id_read(self, obj):
        return obj.menu_root.id if obj.menu_root else None

    def get_parent_id_read(self, obj):
        return obj.parent.id if obj.parent else None

    def get_children(self, obj):
        max_depth = self.context.get('depth', 4)
        current_depth = self.context.get('current_depth', 1)

        if not isinstance(current_depth, int):
            current_depth = 1

        if current_depth >= max_depth:
            return []

        children_qs = obj.children.all().order_by('id')
        serializer = MenuSerializer(
            children_qs,
            many=True,
            context={**self.context, 'current_depth': current_depth + 1}
        )
        return serializer.data

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Nama menu tidak boleh kosong.")
        return value

    def validate_link(self, value):
        if value:
            value = value.strip()
            if not value:
                raise serializers.ValidationError("Link tidak boleh hanya spasi.")
        return value

    def validate(self, data):
        parent = data.get('parent', None)
        name = data.get('name', None)
        link = data.get('link', None)

        # 🔁 Cek jika menu menjadi parent dari dirinya sendiri
        if parent and self.instance:
            if parent == self.instance:
                raise serializers.ValidationError("Menu tidak boleh menjadi parent untuk dirinya sendiri.")

            current = parent
            while current:
                if current == self.instance:
                    raise serializers.ValidationError("Siklus parent-child terdeteksi.")
                current = current.parent

        # ✅ Cek menu duplikat berdasarkan nama (opsional: Anda bisa juga cek kombinasi name + link)
        if name:
            qs = Menu.objects.filter(name__iexact=name.strip())
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)

            if qs.exists():
                raise serializers.ValidationError({"name": "Nama menu sudah digunakan. Harap gunakan nama lain."})

        return data
    
    def create(self, validated_data):
        permissions_data = validated_data.pop('permissions', None)
        menu = Menu.objects.create(**validated_data)
        if permissions_data is not None:
            menu.permissions.set(permissions_data)
        return menu
    
    def update(self, instance, validated_data):
        permissions_data = validated_data.pop('permissions', None)
        
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if permissions_data is not None:
            instance.permissions.set(permissions_data)

        return instance
    
class ProvinceOptionSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="region_name", read_only=True)

    class Meta:
        model = Region
        fields = ["id", "name"]

class NotaryOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notary
        fields = ["id", "full_name"]

class WitnessSerializer(serializers.ModelSerializer):
    province_name = serializers.CharField(source="province.region_name", read_only=True)
    notary_name   = serializers.CharField(source="notary.full_name", read_only=True)

    class Meta:
        model = Witness
        fields = [
            "id","full_name","short_title","full_title","birth_info","nationality",
            "residence","address","rt","rw","subdistrict","district","province",
            "province_name","national_id","notary","notary_name","occupation",
            "gender","marital_status","is_active","created_at","updated_at"
        ]

    def validate_national_id(self, value):
        if not value:
            return value
        qs = Witness.objects.filter(national_id=value)
        if self.instance:  # <-- exclude diri sendiri saat update
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("No. KTP sudah dipakai oleh saksi lain.")
        return value