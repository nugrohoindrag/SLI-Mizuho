from django.apps import AppConfig


class ImportCabangOcConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'import_cabang_oc'
    
    def ready(self):
        import import_cabang_oc.signals
