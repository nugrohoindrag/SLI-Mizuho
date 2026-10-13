from django.db import models

# Create your models here.
class tb_invoice(models.Model):
    id = models.BigAutoField(primary_key=True)
    log_accept = models.CharField(max_length=255)
    tgl_receive_payment = models.DateField(blank=True, null=True)
    log_pay = models.CharField(max_length=255)
    tgl_pay = models.DateField(blank=True, null=True)
    branch = models.ForeignKey('import_cabang.Branch',to_field='branch_code',db_column='branch',on_delete=models.SET_NULL,null=True,blank=True,)
    no_invoice = models.CharField(max_length=255)
    customer_id = models.IntegerField(null=False)
    path_invoice = models.CharField(max_length=255)
    service_cost = models.CharField(max_length=255)
    pnbp_fees = models.CharField(max_length=255)
    kd_voucher = models.CharField(max_length=255)
    tgl_inv = models.DateField(blank=True, null=True)
    agreement_number = models.CharField(max_length=255)
    bank_name = models.CharField(max_length=255)
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)
    class Meta:
        db_table = 'tb_invoice'
        managed = False

class CommonCodes(models.Model):
    code_key = models.CharField(unique=True, max_length=50)
    code_name = models.CharField(max_length=100)
    code_group = models.CharField(max_length=50)
    code_value = models.CharField(max_length=100, blank=True, null=True)
    modified_by_code = models.CharField(max_length=50, blank=True, null=True)
    modified_by_code_user = models.ForeignKey('AdminUser.User', models.DO_NOTHING, blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'common_codes'