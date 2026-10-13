from django.db import models
from django.utils import timezone

class Branch(models.Model):
    branch_code = models.CharField(primary_key=True, max_length=20)
    area = models.CharField(max_length=100, blank=True, null=True)
    branch_name = models.CharField(max_length=150, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    person_in_charge = models.CharField(max_length=50, blank=True, null=True)
    subdistrict = models.CharField(max_length=100, blank=True, null=True)
    district = models.CharField(max_length=100, blank=True, null=True)
    rt = models.CharField(max_length=10, blank=True, null=True)
    rw = models.CharField(max_length=10, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    postal_code = models.CharField(max_length=10, blank=True, null=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    tax_id = models.CharField(max_length=25, blank=True, null=True)
    decree_number = models.CharField(max_length=50, blank=True, null=True)
    decree_date = models.DateField(blank=True, null=True)
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'branches'

    def __str__(self):
        return f"{self.branch_code} - {self.branch_name}"


class BranchDecree(models.Model):
    id = models.AutoField(primary_key=True)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, db_column='branch_code', related_name='decrees')
    decree_number = models.CharField(max_length=100)
    decree_date = models.DateField(blank=True, null=True)
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'branch_decrees'

    def __str__(self):
        return f"Decree {self.decree_number} for {self.branch.branch_code}"


class Notary(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)  # contoh, sesuaikan dengan struktur tabel aslinya

    class Meta:
        managed = False
        db_table = 'notaries'

    def __str__(self):
        return self.name


class NotaryBranchArea(models.Model):
    notary = models.ForeignKey(Notary, on_delete=models.CASCADE, db_column='notary_id', related_name='branch_areas')
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, db_column='branch_code', related_name='notary_areas')

    class Meta:
        managed = False
        db_table = 'notary_branch_areas'
        unique_together = (('notary', 'branch'),)

    def __str__(self):
        return f"Notary {self.notary.id} assigned to Branch {self.branch.branch_code}"

# Create your models here.
class ConsumerMU(models.Model):
    id = models.AutoField(primary_key=True)
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, db_column='branch_code')
    branch_name = models.CharField(max_length=150, blank=True, null=True)
    fiducia_provider = models.CharField(max_length=225, blank=True, null=True)
    customer_type = models.CharField(max_length=50, blank=True, null=True)
    name_on_bpkb = models.CharField(max_length=225, blank=True, null=True)
    bpkb_birth_date = models.DateField(blank=True, null=True)
    bpkb_birth_place = models.CharField(max_length=225, blank=True, null=True)
    bpkb_occupation = models.CharField(max_length=225, blank=True, null=True)
    bpkb_nationality = models.CharField(max_length=50, blank=True, null=True)
    bpkb_address = models.TextField(blank=True, null=True)
    bpkb_rt = models.CharField(max_length=10, blank=True, null=True)
    bpkb_rw = models.CharField(max_length=10, blank=True, null=True)
    bpkb_subdistrict = models.CharField(max_length=225, blank=True, null=True)
    bpkb_district = models.CharField(max_length=225, blank=True, null=True)
    bpkb_city = models.CharField(max_length=225, blank=True, null=True)
    bpkb_province = models.CharField(max_length=225, blank=True, null=True)
    bpkb_postal_code = models.CharField(max_length=10, blank=True, null=True)
    bpkb_ktp = models.CharField(max_length=50, blank=True, null=True)
    debtor_name = models.CharField(max_length=225, blank=True, null=True)
    gender = models.CharField(max_length=10, blank=True, null=True)
    marital_status = models.CharField(max_length=50, blank=True, null=True)
    birth_place = models.CharField(max_length=225, blank=True, null=True)
    birth_date = models.DateField(blank=True, null=True)
    occupation = models.CharField(max_length=225, blank=True, null=True)
    nationality = models.CharField(max_length=50, blank=True, null=True)
    id_type = models.CharField(max_length=50, blank=True, null=True)
    id_number = models.CharField(max_length=50, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    rt_rw = models.CharField(max_length=20, blank=True, null=True)
    subdistrict = models.CharField(max_length=225, blank=True, null=True)
    district = models.CharField(max_length=225, blank=True, null=True)
    city = models.CharField(max_length=225, blank=True, null=True)
    province = models.CharField(max_length=225, blank=True, null=True)
    postal_code = models.CharField(max_length=10, blank=True, null=True)
    company_type = models.CharField(max_length=50, blank=True, null=True)
    company_npwp = models.CharField(max_length=50, blank=True, null=True)
    deed_number = models.CharField(max_length=200, blank=True, null=True)
    deed_date = models.DateField(blank=True, null=True)
    company_director_name = models.CharField(max_length=225, blank=True, null=True)
    decree_date = models.DateField(blank=True, null=True)
    director_gender = models.CharField(max_length=10, blank=True, null=True)
    director_status = models.CharField(max_length=50, blank=True, null=True)
    director_birth_place = models.CharField(max_length=225, blank=True, null=True)
    director_birth_date = models.DateField(blank=True, null=True)
    director_nationality = models.CharField(max_length=50, blank=True, null=True)
    director_id_type = models.CharField(max_length=50, blank=True, null=True)
    director_id_number = models.CharField(max_length=50, blank=True, null=True)
    director_address = models.TextField(blank=True, null=True)
    director_rt_rw = models.CharField(max_length=20, blank=True, null=True)
    director_subdistrict = models.CharField(max_length=225, blank=True, null=True)
    director_district = models.CharField(max_length=225, blank=True, null=True)
    director_city = models.CharField(max_length=225, blank=True, null=True)
    director_province = models.CharField(max_length=225, blank=True, null=True)
    director_occupation = models.CharField(max_length=225, blank=True, null=True)
    director_position = models.CharField(max_length=225, blank=True, null=True)
    director_notary_name = models.CharField(max_length=255, blank=True, null=True)
    notary_city = models.CharField(max_length=225, blank=True, null=True)
    agreement_type = models.CharField(max_length=225, blank=True, null=True)
    agreement_name = models.CharField(max_length=225, blank=True, null=True)
    agreement_number = models.CharField(max_length=50, blank=True, null=True)
    agreement_date = models.DateField(blank=True, null=True)
    spb_date = models.DateField(blank=True, null=True)
    principal_loan = models.CharField(max_length=18, blank=True, null=True)
    guarantee_value = models.CharField(max_length=18, blank=True, null=True)
    installment_start_date = models.DateField(blank=True, null=True)
    tenor = models.IntegerField(blank=True, null=True)
    vehicle_condition = models.CharField(max_length=225, blank=True, null=True)
    brand = models.CharField(max_length=225, blank=True, null=True)
    type = models.CharField(max_length=225, blank=True, null=True)
    color = models.CharField(max_length=50, blank=True, null=True)
    assembly_year = models.IntegerField(blank=True, null=True)
    production_year = models.IntegerField(blank=True, null=True)
    chassis_number = models.CharField(max_length=225, blank=True, null=True, unique=True)
    engine_number = models.CharField(max_length=225, blank=True, null=True, unique=True)
    bpkb_number = models.CharField(max_length=225, blank=True, null=True)
    otr_value = models.CharField(max_length=18, blank=True, null=True)
    pnbp_fee = models.CharField(max_length=18, blank=True, null=True)
    service_fee = models.CharField(max_length=18, blank=True, null=True)
    npwp_provider = models.CharField(max_length=50, blank=True, null=True)
    installment_end_date = models.DateField(blank=True, null=True)
    bpkb_id_number = models.CharField(max_length=50, blank=True, null=True)
    bpkb_npwp_number = models.CharField(max_length=50, blank=True, null=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    receipt_name = models.CharField(max_length=225, blank=True, null=True)
    spouse_name = models.CharField(max_length=225, blank=True, null=True)
    uploader_company = models.CharField(max_length=225, blank=True, null=True)
    upload_group = models.CharField(max_length=225, blank=True, null=True)
    st_process = models.CharField(max_length=50, blank=True, null=True, default='0')
    is_st_used = models.IntegerField(blank=True, null=True, default='1')
    uploader = models.ForeignKey('AdminUser.User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    house_number = models.CharField(max_length=20, blank=True, null=True)
    rw = models.CharField(max_length=10, blank=True, null=True)
    bpkb_name = models.CharField(max_length=225, blank=True, null=True)
    status = models.CharField(max_length=50, blank=True, null=True)
    upload_date = models.DateField(blank=True, null=True)
    document_status = models.CharField(max_length=50, blank=True, null=True, default='0')
    invoice_st = models.CharField(max_length=50, blank=True, null=True, default='0')
    over_credit = models.IntegerField(blank=True, null=True)
    sync = models.IntegerField(blank=True, null=True)
    
    #New Columns
    fiduciary_status = models.CharField(max_length=100, blank=True, null=True)
    rt = models.CharField(max_length=10, blank=True, null=True)
    unit_quantity = models.CharField(max_length=100, blank=True, null=True)
    model = models.CharField(max_length=100, blank=True, null=True)
    go_live_date = models.DateField(blank=True, null=True)
    customer_number = models.CharField(max_length=50, blank=True, null=True)
    agreement_date_words = models.CharField(max_length=255, blank=True, null=True)
    spb_date_words = models.CharField(max_length=255, blank=True, null=True)
    bpkb_birth_date_words = models.CharField(max_length=255, blank=True, null=True)
    birth_date_words = models.CharField(max_length=255, blank=True, null=True)
    otr_value_words = models.CharField(max_length=255, blank=True, null=True)
    principal_loan_words = models.CharField(max_length=255, blank=True, null=True)
    guarantee_value_words = models.CharField(max_length=255, blank=True, null=True)
    
    def __str__(self):
        return self.debtor_name or f"consumers_mu {self.id}"

    def document_status_label(self):
        labels = {
            "1": "Completed",
            "2": "Process",
            "3": "Revision",
        }
        return labels.get(self.document_status, "Uncompleted")
    
    # Cek doc status
    @property
    def is_process_done(self):
        return self.document_status in ["1", "2", "3"]
    
    @property
    def has_legal_document(self):
        return self.legal_documents_mu.exists()
    
    @property
    def can_download_certificate(self):
        doc = self.legal_documents_mu.first()
        return self.is_process_done and doc and bool(doc.certificate_path)

    @property
    def can_download_akta(self):
        doc = self.legal_documents_mu.first()
        return self.is_process_done and doc and bool(doc.pnbp_path)
    
    def save(self, *args, **kwargs):
        if not self.upload_date:
            self.upload_date = (self.created_at or timezone.now()).date()
        super().save(*args, **kwargs)

    class Meta:
        db_table = 'consumers_mu'
        managed = False
        
class ConsumerStatusMU(models.Model):
    consumer = models.OneToOneField(ConsumerMU, on_delete=models.CASCADE)
    status = models.CharField(max_length=50, choices=[
        ('Check', 'Check'),
        ('Clear', 'Clear'),
        ('Paid', 'Paid'),
        ('Done', 'Done'),
    ])
    updated_by = models.ForeignKey(
        'AdminUser.User',
        on_delete=models.SET_NULL,
        null=True,
        db_column='updated_by',
        related_name='consumerstatus_mu_set'  # ✅ Tambahkan ini agar tidak konflik
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'consumer_status_mu'