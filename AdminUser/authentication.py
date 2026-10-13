import pytz
from rest_framework import serializers
from django.contrib.auth.hashers import make_password
from django.contrib.auth import get_user_model, authenticate
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.storage import default_storage
from django.conf import settings
import os, re, logging
from django.utils import timezone
from AdminRole.models import RoleUser
from django.db import transaction
from datetime import timedelta
from .models import User, Branch, Company, Role, Region, LoginHistory, UserBranch
from rest_framework.exceptions import ValidationError
from common.const.role_map import ROLE_KEYS, has_role


class RoleSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["id", "name", "display_name"]


# 🔒 Validasi kekuatan password
def validate_password_strength(password, user=None):
    # Use Django's built-in password validators
    try:
        validate_password(password, user=user)
    except DjangoValidationError as e:
        raise serializers.ValidationError(e.messages)

    # Additional custom validation
    if len(password) < 8:
        raise serializers.ValidationError("Password minimal 8 karakter.")
    if not re.search(r"[A-Z]", password):
        raise serializers.ValidationError("Harus ada huruf besar.")
    if not re.search(r"[a-z]", password):
        raise serializers.ValidationError("Harus ada huruf kecil.")
    if not re.search(r"\d", password):
        raise serializers.ValidationError("Harus ada angka.")
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
        raise serializers.ValidationError("Harus ada simbol.")


# ✅ Self Update Profile Serializer
class SelfUserUpdateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False)
    birth_place = serializers.CharField(
        write_only=True, required=False, allow_blank=True
    )
    birth_date = serializers.DateField(write_only=True, required=False)
    company_id = serializers.IntegerField(write_only=True, required=False)
    province_id = serializers.IntegerField(write_only=True, required=False)
    profile_picture = serializers.ImageField(required=False)

    class Meta:
        model = User
        fields = [
            "name",
            "email",
            "username",
            "national_id",
            "address",
            "occupation",
            "birth_info",
            "city",
            "rw",
            "rt",
            "subdistrict",
            "district",
            "province_id",
            "company_id",
            "password",
            "birth_place",
            "birth_date",
            "profile_picture",
        ]
        read_only_fields = ["birth_info"]

    def validate_password(self, value):
        validate_password_strength(value)
        return value

    def update(self, instance, validated_data):
        if "password" in validated_data:
            password = validated_data.pop("password")
            validate_password(password, user=instance)
            instance.set_password(password)

        company_id = validated_data.pop("company_id", None)
        if company_id is not None:
            try:
                instance.company = Company.objects.get(id=company_id)
            except Company.DoesNotExist:
                raise serializers.ValidationError(
                    {"company_id": "Perusahaan tidak ditemukan."}
                )

        province_id = validated_data.pop("province_id", None)
        if province_id is not None:
            try:
                instance.province = Region.objects.get(
                    id=province_id, region_level="province"
                )
            except Region.DoesNotExist:
                raise serializers.ValidationError(
                    {"province_id": "Provinsi tidak ditemukan."}
                )

        birth_place = validated_data.pop("birth_place", "")
        birth_date = validated_data.pop("birth_date", None)
        if birth_place or birth_date:
            if not birth_place or not birth_date:
                raise serializers.ValidationError(
                    {"Cek Inputan Anda": "Tempat dan tanggal lahir harus lengkap."}
                )
            instance.birth_info = f"{birth_place}, {birth_date.strftime('%d-%m-%Y')}"

        if "profile_picture" in validated_data:
            upload_folder = os.path.join(settings.MEDIA_ROOT, "profile_pictures")
            os.makedirs(upload_folder, exist_ok=True)
            if instance.profile_picture and default_storage.exists(
                instance.profile_picture.name
            ):
                instance.profile_picture.delete(save=False)
            instance.profile_picture = validated_data.pop("profile_picture")

        return super().update(instance, validated_data)

    def validate_name(self, value):
        if not re.match(r"^[A-Za-z\s]+$", value):
            raise serializers.ValidationError(
                "Nama hanya boleh berisi huruf dan spasi."
            )
        return value

    def validate_username(self, value):
        if not re.match(r"^[a-zA-Z0-9._]{3,}$", value):
            raise serializers.ValidationError(
                "Username hanya boleh berisi huruf, angka, titik (.), dan underscore (_), minimal 3 karakter, tanpa spasi."
            )
        return value


# ✅ Change Password Serializer
class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, data):
        user = self.context["request"].user
        if not user.check_password(data.get("old_password")):
            raise serializers.ValidationError({"old_password": "Password lama salah."})
        if data.get("new_password") != data.get("confirm_password"):
            raise serializers.ValidationError(
                {"confirm_password": "Password baru tidak cocok."}
            )
        validate_password_strength(data.get("new_password"), user=user)
        return data

    def save(self, **kwargs):
        user = self.context["request"].user
        new_password = self.validated_data["new_password"]
        user.set_password(new_password)
        user.save(update_fields=["password"])
        return user


# --- Tambahan mini serializer untuk Branch ---
class BranchMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = ["branch_code", "branch_name"]


# --- Full User Serializer (direvisi) ---
class UserSerializer(serializers.ModelSerializer):
    company = serializers.StringRelatedField(read_only=True)
    branch = BranchMiniSerializer(read_only=True)  # ← sebelumnya StringRelatedField
    branches = serializers.SerializerMethodField()
    roles = RoleSimpleSerializer(many=True, read_only=True)
    province_id = serializers.IntegerField(source="province.id", read_only=True)
    province_name = serializers.CharField(source="province.region_name", read_only=True)
    profile_picture = serializers.ImageField(read_only=True)
    otp_created_at = serializers.SerializerMethodField()

    # write-only inputs
    branch_code = serializers.CharField(
        write_only=True, required=False, allow_blank=True, allow_null=True
    )
    branch_codes = serializers.ListField(
        child=serializers.CharField(allow_blank=False),
        write_only=True,
        required=False,
        allow_empty=True,
    )
    company_id = serializers.IntegerField(write_only=True, required=False)
    role_ids = serializers.ListField(
        child=serializers.IntegerField(), write_only=True, required=False
    )
    password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = User
        fields = [
            "id",
            "name",
            "email",
            "username",
            "national_id",
            "address",
            "occupation",
            "birth_info",
            "city",
            "rw",
            "rt",
            "subdistrict",
            "district",
            "province_id",
            "province_name",
            "company",
            "branch",  # ← nested: {branch_code, branch_name}
            "branches",
            "roles",
            "branch_code",
            "branch_codes",
            "company_id",
            "role_ids",
            "password",
            "profile_picture",
            "created_at",
            "updated_at",
            "otp_created_at",
        ]
        read_only_fields = ["company", "branch", "branches", "roles", "created_at", "updated_at", "otp_created_at"]

    def validate_password(self, value):
        validate_password_strength(value)
        return value
    
    def get_otp_created_at(self, obj):
        if obj.otp_created_at:
            tz = pytz.timezone("Asia/Jakarta")
            local_time = obj.otp_created_at.astimezone(tz)
            return local_time.isoformat()
        return None

    def get_branches(self, obj):
        role_names = list(obj.roles.values_list("name", flat=True))
        is_staff_cabang = (
            has_role(role_names, ROLE_KEYS["STAFF_CABANG"])
            and not has_role(role_names, ROLE_KEYS["HO"])
            and not getattr(obj, "is_superuser", False)
        )
        if is_staff_cabang:
            links = getattr(obj, "branch_links", None)
            if links is not None:
                links = list(links.all())
            else:
                links = list(
                    UserBranch.objects
                    .filter(user=obj)
                    .select_related("branch")
                    .order_by("branch__branch_name", "branch_id")
                )
            branches = [link.branch for link in links if link.branch_id]
        else:
            branches = [obj.branch] if getattr(obj, "branch", None) else []
        return BranchMiniSerializer(branches, many=True).data

    def _normalize_branch_codes(self, branch_codes, raw_branch_code=None):
        codes = []
        if branch_codes is not None:
            codes.extend(branch_codes)
        elif raw_branch_code:
            codes.append(raw_branch_code)

        normalized = []
        for code in codes:
            clean = str(code or "").strip()
            if clean and clean not in normalized:
                normalized.append(clean)
        return normalized

    def _get_valid_roles(self, role_ids):
        roles = list(Role.objects.filter(id__in=role_ids))
        if len(roles) != len(set(role_ids)):
            raise serializers.ValidationError(
                {"role_ids": "Beberapa role tidak ditemukan."}
            )
        return roles

    def _is_staff_cabang_role(self, roles, user=None):
        role_names = [role.name for role in roles]
        return (
            has_role(role_names, ROLE_KEYS["STAFF_CABANG"])
            and not has_role(role_names, ROLE_KEYS["HO"])
            and not getattr(user, "is_superuser", False)
        )

    def _set_user_branches(self, user, branch_codes, is_staff_cabang):
        UserBranch.objects.filter(user=user).delete()

        if not branch_codes:
            user.branch = None
            user.save(update_fields=["branch"])
            return

        branches = list(Branch.objects.filter(branch_code__in=branch_codes))
        found_codes = {branch.branch_code for branch in branches}
        missing_codes = [code for code in branch_codes if code not in found_codes]
        if missing_codes:
            raise serializers.ValidationError(
                {"branch_codes": f"Cabang tidak ditemukan: {', '.join(missing_codes)}"}
            )

        branch_by_code = {branch.branch_code: branch for branch in branches}
        ordered_branches = [branch_by_code[code] for code in branch_codes]
        user.branch = ordered_branches[0]
        user.save(update_fields=["branch"])

        if is_staff_cabang:
            UserBranch.objects.bulk_create([
                UserBranch(user=user, branch=branch)
                for branch in ordered_branches
            ])

    @transaction.atomic
    def create(self, validated_data):
        # --- ambil & keluarkan dulu semua write-only ---
        role_ids = validated_data.pop("role_ids", [])  # ← PENTING: pop sebelum create
        raw_branch_code = validated_data.pop("branch_code", None)
        raw_branch_codes = validated_data.pop("branch_codes", None)
        company_id = validated_data.pop("company_id", None)
        raw_password = validated_data.pop("password", None)

        # email lowercase & unik
        email = (validated_data.get("email") or "").lower()
        validated_data["email"] = email
        if User.objects.filter(email=email).exists():
            raise serializers.ValidationError(
                {"email": f"Email '{email}' sudah digunakan."}
            )

        valid_roles = self._get_valid_roles(role_ids) if role_ids else []
        is_staff_cabang = self._is_staff_cabang_role(valid_roles)
        branch_codes = self._normalize_branch_codes(raw_branch_codes, raw_branch_code)
        if not is_staff_cabang:
            branch_codes = branch_codes[:1]
        validated_data["branch"] = None
        if branch_codes:
            try:
                validated_data["branch"] = Branch.objects.get(branch_code=branch_codes[0])
            except Branch.DoesNotExist:
                raise serializers.ValidationError(
                    {"branch_codes": f"Cabang dengan kode '{branch_codes[0]}' tidak ditemukan."}
                )

        # company
        if company_id:
            try:
                validated_data["company"] = Company.objects.get(id=company_id)
            except Company.DoesNotExist:
                raise serializers.ValidationError(
                    {"company_id": "Perusahaan tidak ditemukan."}
                )

        # password
        if raw_password:
            validated_data["password"] = make_password(raw_password)

        # --- baru create user ---
        user = User.objects.create(**validated_data)
        self._set_user_branches(user, branch_codes, is_staff_cabang)

        # roles (opsional)
        if valid_roles:
            for r in valid_roles:
                RoleUser.objects.create(
                    user=user,
                    role_id=r.id,
                    user_type=f"{user.__module__}.{user.__class__.__name__}",
                )

        return user

    @transaction.atomic
    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        role_ids = validated_data.pop('role_ids', None)
        branch_code = validated_data.pop('branch_code', None)
        branch_codes = validated_data.pop('branch_codes', None)
        if role_ids is not None:
            valid_roles = self._get_valid_roles(role_ids)
        else:
            valid_roles = list(instance.roles.all())
        is_staff_cabang = self._is_staff_cabang_role(valid_roles, user=instance)
        instance = super().update(instance, validated_data)
        if password:
            validate_password(password, user=instance)
            instance.set_password(password)
            if hasattr(instance, 'failed_login_attempts'):
                instance.failed_login_attempts = 0
                instance.is_blocked = False
                instance.is_active = True
                instance.last_login = timezone.now()
            instance.save()

        branch_input_provided = branch_codes is not None or branch_code is not None
        if branch_input_provided:
            normalized_branch_codes = self._normalize_branch_codes(branch_codes, branch_code)
            if not is_staff_cabang:
                normalized_branch_codes = normalized_branch_codes[:1]
            self._set_user_branches(
                instance, normalized_branch_codes, is_staff_cabang
            )
        elif role_ids is not None:
            current_code = getattr(instance, "branch_id", None)
            self._set_user_branches(
                instance,
                [current_code] if current_code else [],
                is_staff_cabang,
            )

        if role_ids is not None:
            instance.roles.set(role_ids)
        
        instance.refresh_from_db()
        return instance

# ✅ Register Serializer
class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ["name", "email", "username", "password"]

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Email sudah digunakan.")
        return value

    def validate_password(self, value):
        validate_password_strength(value)
        return value

    def create(self, validated_data):
        validated_data["password"] = make_password(validated_data["password"])
        return User.objects.create(**validated_data)

    def validate_name(self, value):
        if not re.match(r"^[A-Za-z\s]+$", value):
            raise serializers.ValidationError(
                "Nama hanya boleh berisi huruf dan spasi."
            )
        return value

    def validate_username(self, value):
        if not re.match(r"^[a-zA-Z0-9._]{3,}$", value):
            raise serializers.ValidationError(
                "Username hanya boleh berisi huruf, angka, titik (.), dan underscore (_), minimal 3 karakter, tanpa spasi."
            )
        return value

def raise_error(msg):
    raise ValidationError(msg)

class LoginSerializer(serializers.Serializer):
    identifier = serializers.CharField(required=True)
    password = serializers.CharField(write_only=True)

    def _get_client_ip(self):
        request = self.context.get("request")
        if not request:
            return None

        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")

    def validate(self, data):
        identifier = data.get("identifier", "").strip().lower()
        password = data.get("password")

        if not identifier:
            raise_error("Email/username harus diisi.")
        if not password:
            raise_error("Password harus diisi.")

        UserModel = get_user_model()

        try:
            user = (
                UserModel.objects.get(email=identifier)
                if "@" in identifier
                else UserModel.objects.get(username=identifier)
            )
        except UserModel.DoesNotExist:
            raise_error("Email/username atau password salah.")

        last_activity = None

        last_success = (
            LoginHistory.objects
            .filter(user=user, status="success")
            .order_by("-created_at")
            .first()
        )

        if last_success:
            last_activity = last_success.created_at

        if user.last_login:
            if not last_activity or user.last_login > last_activity:
                last_activity = user.last_login

        three_months_ago = timezone.now() - timedelta(days=90)

        if last_activity:
            if last_activity < three_months_ago:
                if user.is_active:
                    user.is_active = False
                    user.save(update_fields=["is_active"])
            else:
                if not user.is_active:
                    user.is_active = True
                    user.save(update_fields=["is_active"])

        if user.is_blocked:
            raise_error("Akun Anda diblokir karena terlalu banyak percobaan login gagal.")

        if not user.check_password(password):
            user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
            just_blocked = False
            if user.failed_login_attempts >= 3:
                user.is_blocked = True
                just_blocked = True
            user.save(update_fields=["failed_login_attempts", "is_blocked"])

            now = timezone.now()

            LoginHistory.objects.create(
                user=user,
                status="failed",
                ip_address=self._get_client_ip(),
                created_at=now,
                updated_at=now,
            )

            if just_blocked:
                LoginHistory.objects.create(
                    user=user,
                    status="blocked",
                    ip_address=self._get_client_ip(),
                    created_at=now,
                    updated_at=now,
                )

            raise_error("Email/username atau password salah.")

        if not user.is_active:
            raise_error("Akun Anda tidak aktif. Silahkan hubungi admin.")

        user.failed_login_attempts = 0
        user.last_login = timezone.now()
        user.save(update_fields=["failed_login_attempts", "last_login"])

        LoginHistory.objects.create(
            user=user,
            status="success",
            ip_address=self._get_client_ip(),
            created_at=timezone.now(),
            updated_at=timezone.now(),
        )

        return {"user": user}

# ✅ Custom Authentication Backend
class EmailOrUsernameBackend(ModelBackend):
    def authenticate(self, request, identifier=None, password=None, **kwargs):
        UserModel = get_user_model()
        identifier = identifier.strip().lower()

        user = None
        try:
            if "@" in identifier:
                user = UserModel.objects.get(email=identifier)
            else:
                user = UserModel.objects.get(username=identifier)
        except UserModel.DoesNotExist:
            return None

        if user and user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
