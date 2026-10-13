from django.db import models
from django.conf import settings

# ✅ Permission
class Permission(models.Model):
    name = models.CharField(max_length=100)

    class Meta:
        managed = False
        db_table = 'permissions'

    def __str__(self):
        return self.name

# ✅ Role
class Role(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)
    display_name = models.CharField(max_length=150, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    permissions = models.ManyToManyField(
        Permission,
        through='RolePermission',
        related_name='roles'
    )

    class Meta:
        managed = False
        db_table = 'roles'

    def __str__(self):
        return self.display_name or self.name

# 🔗 Pivot Role <-> Permission
class RolePermission(models.Model):
    id = models.AutoField(primary_key=True)
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE)

    class Meta:
        managed = False
        db_table = 'role_permissions'
        unique_together = ('role', 'permission')

    def __str__(self):
        return f"{self.role} - {self.permission}"

# 🔗 Pivot Role <-> User
class RoleUser(models.Model):
    id = models.AutoField(primary_key=True)
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    user_type = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'role_user'
        unique_together = ('role', 'user')

    def __str__(self):
        return f"{self.user} - {self.role}"

# 🔗 Pivot Menu <-> Permission
class MenuPermission(models.Model):
    id = models.AutoField(primary_key=True)
    menu = models.ForeignKey('AdminMenu.Menu', on_delete=models.CASCADE)
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE)

    class Meta:
        managed = False
        db_table = 'menu_permission'
        unique_together = ('menu', 'permission')

    def __str__(self):
        return f"{self.menu} - {self.permission}"