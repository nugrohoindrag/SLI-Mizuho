from django.db import models

class VehicleUnit(models.Model):
    vehicle_condition = models.CharField(max_length=50)
    vehicle_brand = models.CharField(max_length=100)
    vehicle_type = models.CharField(max_length=100)
    vehicle_color = models.CharField(max_length=50)
    manufacture_year = models.IntegerField()
    assembly_year = models.IntegerField()
    chassis_number = models.CharField(max_length=100)
    engine_number = models.CharField(max_length=100)
    bpkb_number = models.CharField(max_length=100, null=True, blank=True)
    object_value = models.DecimalField(max_digits=18, decimal_places=2)
    agreement_number = models.CharField(max_length=100)  # <-- cocokkan dengan SQL

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'vehicle_units'
        verbose_name = 'Vehicle Unit'
        verbose_name_plural = 'Vehicle Units'

    def __str__(self):
        return f"{self.vehicle_brand} - {self.vehicle_type} ({self.chassis_number})"