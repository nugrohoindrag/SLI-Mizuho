from django.db import models
from import_cabang.models import Consumer, ChangeOfFiduciary
from import_cabang_oc.models import ConsumerOC
from import_cabang_mu.models import ConsumerMU
from AdminUser.models import User

class Companies(models.Model):
    company_name = models.CharField(max_length=150)
    company_address = models.TextField(blank=True, null=True)
    postal_code = models.CharField(max_length=20, blank=True, null=True)
    phone = models.CharField(max_length=30, blank=True, null=True)
    fax = models.CharField(max_length=30, blank=True, null=True)
    email = models.CharField(max_length=100, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    establishment_date = models.DateField(blank=True, null=True)
    establishment_number = models.CharField(max_length=100, blank=True, null=True)
    establishment_notary = models.CharField(max_length=150, blank=True, null=True)
    notary_domicile = models.CharField(max_length=150, blank=True, null=True)
    legal_decree = models.CharField(max_length=100, blank=True, null=True)
    commissioner_name = models.CharField(max_length=150, blank=True, null=True)
    commissioner_gender = models.CharField(max_length=10, blank=True, null=True)
    commissioner_birth_info = models.CharField(max_length=100, blank=True, null=True)
    commissioner_occupation = models.CharField(max_length=100, blank=True, null=True)
    commissioner_residence = models.TextField(blank=True, null=True)
    commissioner_city = models.CharField(max_length=100, blank=True, null=True)
    commissioner_rt = models.CharField(max_length=20, blank=True, null=True)
    commissioner_rw = models.CharField(max_length=20, blank=True, null=True)
    commissioner_subdistrict = models.CharField(max_length=100, blank=True, null=True)
    commissioner_district = models.CharField(max_length=100, blank=True, null=True)
    commissioner_nik = models.CharField(max_length=50, blank=True, null=True)
    commissioner_nationality = models.CharField(max_length=50, blank=True, null=True)
    contract_number = models.CharField(max_length=100, blank=True, null=True)
    contract_date = models.DateField(blank=True, null=True)
    modified_by = models.IntegerField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'companies'

class Notaries(models.Model):
    user = models.ForeignKey('AdminUser.User', models.DO_NOTHING)
    firm_name = models.CharField(max_length=150, blank=True, null=True)
    full_name = models.CharField(max_length=150, blank=True, null=True)
    firm_address = models.TextField(blank=True, null=True)
    city_name = models.CharField(max_length=100, blank=True, null=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    website = models.CharField(max_length=100, blank=True, null=True)
    register_date = models.DateField(blank=True, null=True)
    registered_by = models.ForeignKey('AdminUser.User', models.DO_NOTHING, db_column='registered_by', related_name='notaries_registered_by_set', blank=True, null=True)
    is_active = models.IntegerField(blank=True, null=True)
    max_assignment = models.IntegerField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)
    full_title = models.CharField(max_length=100, blank=True, null=True)
    short_title = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'notaries'

class LegalDocuments(models.Model):
    sequence_number = models.IntegerField(blank=True, null=True)
    consumer = models.ForeignKey(Consumer, models.DO_NOTHING, related_name='akta_legal_documents')
    document_date = models.DateField(blank=True, null=True)
    document_number = models.CharField(max_length=100, blank=True, null=True)
    document_time = models.TimeField(blank=True, null=True)
    notary = models.ForeignKey('Notaries', models.DO_NOTHING, blank=True, null=True)
    power_receiver_name = models.CharField(max_length=150, blank=True, null=True)
    ahu = models.CharField(max_length=100, blank=True, null=True)
    voucher_code = models.CharField(max_length=50, blank=True, null=True)
    certificate_number = models.CharField(max_length=100, blank=True, null=True)
    certificate_date = models.DateField(blank=True, null=True)
    power_user = models.ForeignKey('AdminUser.User', models.DO_NOTHING, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    certificate_path = models.TextField(blank=True, null=True)
    pnbp_path = models.TextField(blank=True, null=True)
    voucher_input = models.TextField(blank=True, null=True)
    certificate_input = models.TextField(blank=True, null=True)
    payment_date = models.DateField(blank=True, null=True)
    business_entity = models.ForeignKey(Companies, models.DO_NOTHING, blank=True, null=True)
    business_entity_director = models.ForeignKey(Companies, models.DO_NOTHING, related_name='legaldocuments_business_entity_director_set', blank=True, null=True)
    service_fee = models.DecimalField(max_digits=18, decimal_places=2, blank=True, null=True)
    pnbp_fee = models.DecimalField(max_digits=18, decimal_places=2, blank=True, null=True)
    registration_date = models.DateField(blank=True, null=True)
    billing_id = models.IntegerField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'legal_documents'

class LegalDocumentsPF(models.Model):
    sequence_number = models.IntegerField(blank=True, null=True)
    change_of_fiduciary = models.ForeignKey(ChangeOfFiduciary, models.DO_NOTHING, related_name='legal_documents_pf')
    document_date = models.DateField(blank=True, null=True)
    document_number = models.CharField(max_length=100, blank=True, null=True)
    document_time = models.TimeField(blank=True, null=True)
    notary = models.ForeignKey('Notaries', models.DO_NOTHING, blank=True, null=True)
    power_receiver_name = models.CharField(max_length=150, blank=True, null=True)
    ahu = models.CharField(max_length=100, blank=True, null=True)
    voucher_code = models.CharField(max_length=50, blank=True, null=True)
    certificate_number = models.CharField(max_length=100, blank=True, null=True)
    certificate_date = models.DateField(blank=True, null=True)
    power_user = models.ForeignKey('AdminUser.User', models.DO_NOTHING, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    certificate_path = models.TextField(blank=True, null=True)
    pnbp_path = models.TextField(blank=True, null=True)
    voucher_input = models.TextField(blank=True, null=True)
    certificate_input = models.TextField(blank=True, null=True)
    payment_date = models.DateField(blank=True, null=True)
    business_entity = models.ForeignKey(Companies, models.DO_NOTHING, blank=True, null=True)
    business_entity_director = models.ForeignKey(Companies, models.DO_NOTHING, related_name='legaldocumentspf_business_entity_director_set', blank=True, null=True)
    service_fee = models.DecimalField(max_digits=18, decimal_places=2, blank=True, null=True)
    pnbp_fee = models.DecimalField(max_digits=18, decimal_places=2, blank=True, null=True)
    registration_date = models.DateField(blank=True, null=True)
    billing_id = models.IntegerField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'legal_documents_pf'

class LegalDocumentsOC(models.Model):
    sequence_number = models.IntegerField(blank=True, null=True)
    consumer = models.ForeignKey(ConsumerOC, models.DO_NOTHING)
    document_date = models.DateField(blank=True, null=True)
    document_number = models.CharField(max_length=100, blank=True, null=True)
    document_time = models.TimeField(blank=True, null=True)
    notary = models.ForeignKey('Notaries', models.DO_NOTHING, blank=True, null=True)
    power_receiver_name = models.CharField(max_length=150, blank=True, null=True)
    ahu = models.CharField(max_length=100, blank=True, null=True)
    voucher_code = models.CharField(max_length=50, blank=True, null=True)
    certificate_number = models.CharField(max_length=100, blank=True, null=True)
    certificate_date = models.DateField(blank=True, null=True)
    power_user = models.ForeignKey(User, models.DO_NOTHING, blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)
    certificate_path = models.TextField(blank=True, null=True)
    pnbp_path = models.TextField(blank=True, null=True)
    voucher_input = models.TextField(blank=True, null=True)
    certificate_input = models.TextField(blank=True, null=True)
    payment_date = models.DateField(blank=True, null=True)
    business_entity = models.ForeignKey(Companies, models.DO_NOTHING, blank=True, null=True)
    business_entity_director = models.ForeignKey(Companies, models.DO_NOTHING, related_name='legaldocumentsoc_business_entity_director_set', blank=True, null=True)
    service_fee = models.DecimalField(max_digits=18, decimal_places=2, blank=True, null=True)
    pnbp_fee = models.DecimalField(max_digits=18, decimal_places=2, blank=True, null=True)
    registration_date = models.DateField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'legal_documents_oc'

class LegalDocumentsMu(models.Model):
    sequence_number = models.IntegerField(blank=True, null=True)
    consumer = models.ForeignKey(ConsumerMU, models.DO_NOTHING)
    document_date = models.DateField(blank=True, null=True)
    document_number = models.CharField(max_length=100, blank=True, null=True)
    document_time = models.TimeField(blank=True, null=True)
    notary = models.ForeignKey('Notaries', models.DO_NOTHING, blank=True, null=True)
    power_receiver_name = models.CharField(max_length=150, blank=True, null=True)
    ahu = models.CharField(max_length=100, blank=True, null=True)
    voucher_code = models.CharField(max_length=50, blank=True, null=True)
    certificate_number = models.CharField(max_length=100, blank=True, null=True)
    certificate_date = models.DateField(blank=True, null=True)
    power_user = models.ForeignKey(User, models.DO_NOTHING, blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)
    certificate_path = models.TextField(blank=True, null=True)
    voucher_input = models.TextField(blank=True, null=True)
    certificate_input = models.TextField(blank=True, null=True)
    payment_date = models.DateField(blank=True, null=True)
    business_entity = models.ForeignKey(Companies, models.DO_NOTHING, blank=True, null=True)
    business_entity_director = models.ForeignKey(Companies, models.DO_NOTHING, related_name='legaldocumentsmu_business_entity_director_set', blank=True, null=True)
    service_fee = models.DecimalField(max_digits=18, decimal_places=2, blank=True, null=True)
    pnbp_fee = models.DecimalField(max_digits=18, decimal_places=2, blank=True, null=True)
    registration_date = models.DateField(blank=True, null=True)
    billing_id = models.IntegerField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'legal_documents_mu'

class AktaMinutaLog(models.Model):
    agreement_number = models.CharField(max_length=100)
    pic = models.ForeignKey('AdminUser.User', on_delete=models.SET_NULL, null=True)
    print_date = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.agreement_number

    class Meta:
        db_table = 'akta_minuta_log'
        
class AktaMinutaLogOC(models.Model):
    agreement_number = models.CharField(max_length=100)
    pic = models.ForeignKey('AdminUser.User', on_delete=models.SET_NULL, null=True)
    print_date = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.agreement_number

    class Meta:
        db_table = 'akta_minuta_log_oc'
        
class AktaMinutaLogMU(models.Model):
    agreement_number = models.CharField(max_length=100)
    pic = models.ForeignKey('AdminUser.User', on_delete=models.SET_NULL, null=True)
    print_date = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.agreement_number

    class Meta:
        db_table = 'akta_minuta_log_mu'

class AktaMinutaLogPF(models.Model):
    agreement_number = models.CharField(max_length=100)
    pic = models.ForeignKey('AdminUser.User', on_delete=models.SET_NULL, null=True)
    print_date = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.agreement_number

    class Meta:
        db_table = 'akta_minuta_log_pf'

class ConsumerStatus(models.Model):
    consumer = models.OneToOneField(Consumer, on_delete=models.CASCADE)
    status = models.CharField(max_length=50, choices=[
        ('Check', 'Check'),
        ('Clear', 'Clear'),
        ('Paid', 'Paid'),
        ('Done', 'Done'),
    ])
    updated_by = models.ForeignKey('AdminUser.User', on_delete=models.SET_NULL, null=True, db_column='updated_by')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'consumer_status'

class CertificateDownloadLogs(models.Model):
    user = models.ForeignKey('AdminUser.User', models.DO_NOTHING)
    agreement_number = models.CharField(max_length=100)
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'certificate_download_logs'
        
class DeedDownloadLogs(models.Model):
    user = models.ForeignKey('AdminUser.User', models.DO_NOTHING)
    agreement_number = models.CharField(max_length=100)
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'deed_download_logs'