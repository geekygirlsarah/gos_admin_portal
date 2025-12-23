from django.apps import AppConfig


class ProgramsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "programs"

    def ready(self):
        # Import signals to ensure role groups are created/maintained
        from . import signals  # noqa: F401
