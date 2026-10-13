from rest_framework import serializers
from import_cabang.models import Consumer

class ConsumerInvoiceSerializer(serializers.ModelSerializer):
    branch_code = serializers.CharField(source='branch.branch_code', read_only=True)
    branch_name = serializers.CharField(read_only=True)
    agreement_number = serializers.CharField(read_only=True)
    debtor_name = serializers.CharField(read_only=True)
    upload_date = serializers.DateField(read_only=True)
    document_status = serializers.CharField(read_only=True)
    invoice_st = serializers.CharField(read_only=True)
    principal_loan = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    pnbp_fee = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    service_fee = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    status = serializers.CharField(read_only=True)

    class Meta:
        model = Consumer
        fields = [
            'id',
            'branch_code',
            'branch_name',
            'agreement_number',
            'debtor_name',
            'upload_date',
            'document_status',
            'invoice_st',
            'principal_loan',
            'pnbp_fee',
            'service_fee',
            'status',
        ]