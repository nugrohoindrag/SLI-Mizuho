from django.db import models
from import_cabang.models import Consumer

class ConsumerDocument(models.Model):
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
        db_table = 'consumer_documents'
        managed = False
        
class FileProcessings(models.Model):
    file_name = models.CharField(max_length=255)
    status = models.CharField(max_length=50, blank=True, null=True)
    old_path = models.TextField(blank=True, null=True)
    new_location = models.TextField(blank=True, null=True)
    uploader_company = models.CharField(max_length=100, blank=True, null=True)
    modified_by = models.ForeignKey('AdminUser.User', models.DO_NOTHING, db_column='modified_by', blank=True, null=True)
    category = models.CharField(max_length=2, null=False, blank=False)
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'file_processings'