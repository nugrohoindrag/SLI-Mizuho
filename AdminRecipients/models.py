from django.db import models
from django.core.validators import RegexValidator

class Recipient(models.Model):
    business_entity_name = models.CharField(max_length=150, verbose_name="Nama Badan Usaha")
    email = models.EmailField(max_length=150)

    phone_number = models.CharField(
        max_length=20,
        validators=[RegexValidator(regex=r'^\d+$', message='Nomor telepon hanya boleh berisi angka.')],
        blank=True,
        null=True,
        verbose_name="Nomor Telepon"
    )

    address = models.TextField(verbose_name="Alamat")
    rt = models.CharField(max_length=10)
    rw = models.CharField(max_length=10)
    subdistrict = models.CharField(max_length=100, verbose_name="Kelurahan")
    district = models.CharField(max_length=100, verbose_name="Kecamatan")
    city = models.CharField(max_length=100, verbose_name="Kota")
    province = models.CharField(max_length=100)

    postal_code = models.CharField(
        max_length=10,
        validators=[RegexValidator(regex=r'^\d+$', message='Kode pos hanya boleh berisi angka.')],
        verbose_name="Kode Pos"
    )

    decree_number = models.CharField(max_length=100, verbose_name="Nomor SK")
    decree_date = models.DateField(verbose_name="Tanggal SK")
    decree_date_end = models.DateField(verbose_name="Tanggal SK akhir")
    tax_number = models.CharField(max_length=25, verbose_name="NPWP")
    ahu_decree_number = models.CharField(max_length=100, verbose_name="Nomor SK AHU")
    pn = models.CharField(max_length=50, blank=True, null=True)

    giver_tax_number = models.CharField(max_length=25, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'recipients'
        ordering = ['-created_at']
        verbose_name = "Penerima Fidusia"
        verbose_name_plural = "Penerima Fidusia"

    def __str__(self):
        return f"{self.business_entity_name} ({self.decree_number})"
