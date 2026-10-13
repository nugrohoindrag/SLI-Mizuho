from django.db import models

class TemporaryUpdatePF(models.Model):
    id = models.AutoField(primary_key=True)
    category = models.CharField(max_length=100)
    agreement_number = models.CharField(max_length=100)
    replacement_value = models.TextField()
    is_approved = models.BooleanField(default=False)
    update_status = models.CharField(max_length=50)
    uploaded_by = models.ForeignKey('AdminUser.User',on_delete=models.SET_NULL,null=True,db_column='uploaded_by',related_name='uploaded_temp_updates_pf')
    approved_by = models.ForeignKey('AdminUser.User',on_delete=models.SET_NULL,null=True,db_column='approved_by',related_name='approved_temp_updates_pf')
    created_at = models.DateTimeField(auto_now=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'temporary_updates_pf'
        managed = False