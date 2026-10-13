from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from django.utils.timezone import now

# 🏢 Company
class Company(models.Model):
    id = models.AutoField(primary_key=True)
    company_name = models.CharField(max_length=100)

    def __str__(self):
        return self.company_name

    class Meta:
        managed = False
        db_table = 'companies'


# 🏢 Branch
class Branch(models.Model):
    branch_code = models.CharField(max_length=20, primary_key=True)
    branch_name = models.CharField(max_length=100)

    def __str__(self):
        return self.branch_name

    class Meta:
        managed = False
        db_table = 'branches'


# 🔐 Permission
class Permission(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)
    display_name = models.CharField(max_length=150, null=True)
    description = models.TextField(null=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.display_name or self.name

    class Meta:
        db_table = 'permissions'
        managed = False
        ordering = ['id']


# 🧑‍⚖️ Role
class Role(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)
    display_name = models.CharField(max_length=150, null=True)
    description = models.TextField(null=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    permissions = models.ManyToManyField(
        Permission,
        through='RolePermission',
        related_name='roles'
    )

    def __str__(self):
        return self.display_name or self.name

    def has_permission(self, perm_name):
        return self.permissions.filter(name=perm_name).exists()

    class Meta:
        db_table = 'roles'
        managed = False


# 🔗 Pivot Table: Role <-> Permission
class RolePermission(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE)

    class Meta:
        db_table = 'role_permissions'
        managed = False
        unique_together = ('role', 'permission')


# 🧑 Custom User Manager
class UserManager(BaseUserManager):
    def create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError('Email wajib diisi.')
        if not password:
            raise ValueError('Password wajib diisi.')

        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)

        validate_password(password, user=user)
        user.set_password(password)

        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        return self.create_user(email, password, **extra_fields)
    
    def set_secure_password(self, raw_password):
        validate_password(raw_password, self)
        self.set_password(raw_password)


# 🌎 Region
class Region(models.Model):
    id = models.AutoField(primary_key=True)
    region_code = models.CharField(max_length=50, unique=True)
    region_name = models.CharField(max_length=100)
    region_capital = models.CharField(max_length=100, null=True, blank=True)
    region_level = models.CharField(max_length=50, null=True, blank=True)
    is_default = models.IntegerField(default=0, null=True, blank=True)
    modified_by = models.ForeignKey('User', null=True, blank=True, on_delete=models.SET_NULL, db_column='modified_by')
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.region_name

    class Meta:
        db_table = 'regions'
        managed = False


# 🧑 User
class User(AbstractBaseUser, PermissionsMixin):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=150)
    email = models.EmailField(unique=True)
    username = models.CharField(max_length=100, null=True, blank=True)
    profile_picture = models.ImageField(upload_to='profile_pictures/', blank=True, null=True)
    last_login = models.DateTimeField(null=True, blank=True)
    company = models.ForeignKey(Company, null=True, on_delete=models.SET_NULL, db_column='company_id')
    branch = models.ForeignKey(Branch, null=True, on_delete=models.SET_NULL, db_column='branch_code', to_field='branch_code')
    province = models.ForeignKey(Region, null=True, blank=True, on_delete=models.SET_NULL, db_column='province_id')

    national_id = models.CharField(max_length=50, null=True, blank=True)
    address = models.TextField(null=True, blank=True)
    occupation = models.CharField(max_length=100, null=True, blank=True)
    birth_info = models.CharField(max_length=100, null=True, blank=True)
    city = models.CharField(max_length=100, null=True, blank=True)
    rw = models.CharField(max_length=10, null=True, blank=True)
    rt = models.CharField(max_length=10, null=True, blank=True)
    subdistrict = models.CharField(max_length=100, null=True, blank=True)
    district = models.CharField(max_length=100, null=True, blank=True)

    roles = models.ManyToManyField(Role, through='RoleUser', related_name='users')

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    
    failed_login_attempts = models.IntegerField(default=0)
    is_blocked = models.BooleanField(default=False)
    count_last_login = models.IntegerField(default=0)
    otp_code = models.CharField(max_length=128, null=True, blank=True) #new
    otp_created_at = models.DateTimeField(null=True, blank=True) #new
    otp_attempts = models.IntegerField(default=0) #new
    
    def is_superadmin(self):
        return self.roles.filter(name='superadministrator').exists()

    def is_admin(self):
        return self.roles.filter(name='administrator').exists()

    def is_admin_or_superadmin(self):
        return self.is_admin() or self.is_superadmin()

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    groups = None
    user_permissions = None

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['name']

    def __str__(self):
        return (
            getattr(self, "username", None)
            or getattr(self, "name", None)
            or getattr(self, "email", None)
            or f"User#{self.pk}"
        )

    def has_permission(self, perm_name):
        return self.roles.filter(permissions__name=perm_name).exists()

    class Meta:
        db_table = 'users'
        managed = False
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        ordering = ['-created_at']
    
    def __str__(self):
        return self.username


class UserBranch(models.Model):
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, db_column='user_id', related_name='branch_links')
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, db_column='branch_code', to_field='branch_code')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'user_branches'
        managed = False
        unique_together = ('user', 'branch')


# 🔗 Pivot Table: Role <-> User
class RoleUser(models.Model):
    id = models.AutoField(primary_key=True)
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='role_links')
    user_type = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        db_table = 'role_user'
        managed = False
        unique_together = ('role', 'user')
        
class LoginHistory(models.Model):
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, db_column='user_id')
    status = models.CharField(max_length=10)  # success / failed
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'login_history'
        managed = False
