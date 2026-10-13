from django.db import models
from django.utils import timezone
from django.conf import settings

try:
    from AdminPermission.models import Permission
except ImportError:
    Permission = None
    
class Root(models.Model):
    root_name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'root'

    def __str__(self):
        return self.root_name

class Menu(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100, db_column='menu_name')
    link = models.CharField(max_length=255, null=True, blank=True, db_column='menu_link')
    icon_css = models.CharField(max_length=100, null=True, blank=True, db_column='menu_icon_css')
    icon_html = models.TextField(null=True, blank=True, db_column='menu_icon')

    # Tambahan relasi ke tabel Root
    menu_root = models.ForeignKey(
        'Root',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column='root_id',  # ✅ Ganti nama kolom yang valid
        related_name='menus'
    )

    # Parent-child relationship antar menu
    parent = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        db_column='menu_root',
        related_name='children'
    )

    level = models.IntegerField(default=1, db_column='menu_level')

    # Many-to-Many relationship dengan Permission
    permissions = models.ManyToManyField(
        'AdminPermission.Permission',
        db_table='menu_permission',
        related_name='menus'
    )

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'menus'
        managed = False  # Ubah ke True jika kamu ingin Django mengelola tabel ini
        ordering = ['id']

    def __str__(self):
        return self.name