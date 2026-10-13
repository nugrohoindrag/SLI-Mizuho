from django.apps import AppConfig


class ImportCabangMuConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'import_cabang_mu'
    
    def ready(self):
        import import_cabang_mu.signals