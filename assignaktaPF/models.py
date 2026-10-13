from django.db import models

class LegalDocumentPF(models.Model):
    sequence_number = models.IntegerField(null=True, blank=True)
    consumer = models.ForeignKey('change_fiduciary.change_of_fiduciary', db_column='change_of_fiduciary_id', null=True, blank=True, on_delete=models.SET_NULL, related_name='legal_documents_pf')
    document_date = models.DateField(null=True, blank=True)
    document_number = models.CharField(max_length=100, null=True, blank=True)
    document_time = models.TimeField(null=True, blank=True)
    notary = models.ForeignKey('AdminNotaries.Notary',null=True,blank=True,on_delete=models.SET_NULL,related_name='legal_documents_pf')
    power_receiver_name = models.CharField(max_length=150, null=True, blank=True)
    ahu = models.CharField(max_length=100, null=True, blank=True)
    voucher_code = models.CharField(max_length=50, null=True, blank=True)
    certificate_number = models.CharField(max_length=100, null=True, blank=True)
    certificate_date = models.DateField(null=True, blank=True)
    power_user = models.ForeignKey('AdminProxies.AdminProxy', null=True, blank=True, on_delete=models.SET_NULL, related_name='legal_documents_pf', db_column='power_user_id')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    certificate_path = models.TextField(null=True, blank=True)
    pnbp_path = models.TextField(null=True, blank=True)
    voucher_input = models.TextField(null=True, blank=True)
    certificate_input = models.TextField(null=True, blank=True)
    payment_date = models.DateField(null=True, blank=True)
    business_entity = models.ForeignKey('AdminUser.Company',null=True,blank=True,on_delete=models.SET_NULL,related_name='legal_documents_entity_pf')
    business_entity_director = models.ForeignKey('AdminUser.Company',null=True,blank=True,on_delete=models.SET_NULL,related_name='legal_documents_director_pf')
    service_fee = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    pnbp_fee = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    registration_date = models.DateField(null=True, blank=True)
    billing_id = models.IntegerField(null=True, blank=True)
    recipient = models.ForeignKey('AdminRecipients.Recipient',null=True, blank=True,on_delete=models.SET_NULL,db_column='recipient_id',related_name='legal_documents_as_recipient_pf')

    class Meta:
        db_table = 'legal_documents_pf'
        managed = False
        verbose_name = "Legal Document PF"
        verbose_name_plural = "Legal Documents PF"
        #unique_together = [('change_of_fiduciary', 'document_number')]