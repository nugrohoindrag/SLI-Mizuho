from django.db import models

class AktaType(models.TextChoices):
    REGULER = 'reguler', 'Reguler'
    OC      = 'oc',      'Over Credit'
    MU      = 'mu',      'Multi Unit'
    PF      = 'pf',      'Perubahan Fidusia'

class DocType(models.TextChoices):
    AKTA       = 'akta',       'Akta'
    SALINAN    = 'salinan',    'Salinan'

class CustomerType(models.TextChoices):
    INDIVIDUAL   = 'I',   'Personal'
    COMPANY = 'C', 'PT'

class AgreementType(models.TextChoices):
    Konvensional = 'konve', 'Konvensional'
    Syariah = 'syariah', 'Syariah'