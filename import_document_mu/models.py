from django.db import models

# Create your models here.
class ConsumerDocumentMU(models.Model):
    id = models.AutoField(primary_key=True)
    agreement_number = models.CharField(max_length=100, unique=True)
    consumer_name = models.CharField(max_length=150)
    agreement_date = models.DateField()
    document_type = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)

    file_url = models.TextField(null=True, blank=True)

    modified_by = models.ForeignKey(
        'AdminUser.User',
        models.SET_NULL,
        db_column='modified_by',
        null=True,
        blank=True
    )

    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()


    class Meta:
        db_table = 'consumer_documents_mu'
        managed = False