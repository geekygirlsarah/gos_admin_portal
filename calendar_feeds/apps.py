from django.apps import AppConfig


class CalendarFeedsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "calendar_feeds"
    verbose_name = "Calendar Feeds"

    def ready(self):
        from . import signals  # noqa: F401
