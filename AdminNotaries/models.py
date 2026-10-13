from django.db import models
from django.core.validators import RegexValidator
from AdminUser.models import User

class Notary(models.Model):
    STATUS_CHOICES = [
        ('aktif', 'Aktif'),
        ('tidak_aktif', 'Tidak Aktif'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    firm_name = models.CharField(max_length=100, verbose_name="Nama Alias")
    full_name = models.CharField(max_length=200, verbose_name="Nama Lengkap")
    short_title = models.CharField(max_length=100, blank=True, null=True, verbose_name="Gelar Pendek")
    full_title = models.CharField(max_length=255, blank=True, null=True, verbose_name="Gelar Panjang")
    email = models.EmailField(unique=True, verbose_name="Email")
    website = models.URLField(blank=True, null=True, verbose_name="Website")

    phone_number = models.CharField(
        max_length=15,
        validators=[RegexValidator(regex=r'^\d+$', message='Nomor telepon hanya boleh berisi angka.')],
        blank=True,
        null=True,
        verbose_name="Telepon"
    )

    firm_address = models.TextField(blank=True, null=True, verbose_name="Alamat")
    city_name = models.CharField(max_length=100, blank=True, null=True, verbose_name="Kota")
    website = models.URLField(blank=True, null=True, verbose_name="Website")

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        verbose_name="Status"
    )

    register_date = models.DateTimeField(auto_now_add=True, verbose_name="Tanggal Daftar")
    updated_at = models.DateTimeField(auto_now=True)

    is_active = models.IntegerField(default=1)
    zone = models.CharField(max_length=4, blank=True, null=True, verbose_name="Zona")

    class Meta:
        db_table = 'notaries'
        ordering = ['-register_date']
        verbose_name = "Notaris"
        verbose_name_plural = "Data Notaris"

    def __str__(self):
        return f"{self.firm_name} ({self.full_name})"

    @property
    def status_label(self):
        return dict(self.STATUS_CHOICES).get(self.status, 'Tidak Diketahui')