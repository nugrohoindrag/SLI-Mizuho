from django.db import models
from AdminNotaries.models import Notary

class Province(models.Model):
    region_name = models.CharField(max_length=100)

    class Meta:
        managed = False
        db_table = 'regions'

    def __str__(self):
        return self.region_name

# class Notary(models.Model):
#     name = models.CharField(max_length=150)

#     class Meta:
#         managed = False
#         db_table = 'notaries'

#     def __str__(self):
#         return self.name

class Witness(models.Model):
    full_name = models.CharField(max_length=150)
    birth_info = models.CharField(max_length=100, blank=True, null=True)
    nationality = models.CharField(max_length=50, blank=True, null=True)
    residence = models.CharField(max_length=150, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    rt = models.CharField(max_length=10, blank=True, null=True)
    rw = models.CharField(max_length=10, blank=True, null=True)
    subdistrict = models.CharField(max_length=100, blank=True, null=True)
    district = models.CharField(max_length=100, blank=True, null=True)
    occupation = models.CharField(max_length=128, blank=True, null=True)

    province = models.ForeignKey(
        Province,
        on_delete=models.SET_NULL,
        null=True,
        db_column='province_id'
    )

    national_id = models.CharField(max_length=50, blank=True, null=True, unique=True)
    notary = models.ForeignKey(Notary, on_delete=models.SET_NULL, null=True, db_column='notary_id')

    is_active = models.IntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    gender = models.CharField(max_length=10, blank=True, null=True)
    marital_status = models.CharField(max_length=50, blank=True, null=True)
    short_title = models.CharField(max_length=50, blank=True, null=True)
    full_title = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        db_table = 'witnesses'

    def __str__(self):
        return self.full_name