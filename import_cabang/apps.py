from django.apps import AppConfig


class ImportCabangConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'import_cabang'

    def ready(self):
        import import_cabang.signals