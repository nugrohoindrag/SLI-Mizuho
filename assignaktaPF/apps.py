from django.apps import AppConfig

class AssignaktapfConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'assignaktaPF'

    def ready(self):  # ✅ HARUS di dalam class
        import assignaktaPF.signals