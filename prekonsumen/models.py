# prekonsumen/models.py

from django.db import models

class NotaryBranchArea(models.Model):
    id = models.AutoField(primary_key=True)
    notary = models.ForeignKey('AdminNotaries.Notary', on_delete=models.CASCADE)
    branch = models.ForeignKey(
        'import_cabang.Branch',
        to_field='branch_code',
        db_column='branch_code',
        on_delete=models.CASCADE
    )

    class Meta:
        db_table = 'notary_branch_areas'
        unique_together = ('notary', 'branch')
        managed = False