from django.db import models
from django.core.validators import RegexValidator

# Validator untuk KTP 16 digit angka
ktp_validator = RegexValidator(
    regex=r'^\d{16}$',
    message='Nomor KTP harus terdiri dari 16 digit angka.'
)

# Validator untuk RT/RW 1-3 digit angka
rt_rw_validator = RegexValidator(
    regex=r'^\d{1,3}$',
    message='RT/RW hanya boleh terdiri dari angka maksimal 3 digit.'
)

class AdminProxy(models.Model):
    full_name = models.CharField(max_length=255)
    birth_info = models.CharField(max_length=255)
    nationality = models.CharField(max_length=100)
    residence = models.CharField(max_length=150, null=True)
    job = models.CharField(max_length=50, null=True)
    address = models.TextField()
    rt = models.CharField(max_length=3, validators=[rt_rw_validator])
    rw = models.CharField(max_length=3, validators=[rt_rw_validator])
    subdistrict = models.CharField(max_length=100)
    district = models.CharField(max_length=100)
    province = models.ForeignKey('AdminUser.Region',to_field='region_code',on_delete=models.SET_NULL,null=True,db_column='province_id')
    national_id = models.CharField(max_length=16, unique=True, validators=[ktp_validator])
    notary = models.ForeignKey('AdminNotaries.Notary', on_delete=models.SET_NULL, null=True, db_column='notary_id')
    is_active = models.IntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    full_title = models.CharField(max_length=255)
    short_title = models.CharField(max_length=100)
    active_from = models.IntegerField(null=True)
    active_until = models.IntegerField(null=True)

    class Meta:
        db_table = 'power_of_attorneys'
        ordering = ['-created_at']
        verbose_name = "Penerima Kuasa"
        verbose_name_plural = "Penerima Kuasa"

    def __str__(self):
        return self.full_name

    @property
    def birth_place(self):
        return self.birth_info.split(',')[0].strip() if self.birth_info and ',' in self.birth_info else ''

    @property
    def birth_date(self):
        import datetime
        try:
            return datetime.datetime.strptime(self.birth_info.split(',')[1].strip(), '%d-%m-%Y').date()
        except:
            return None