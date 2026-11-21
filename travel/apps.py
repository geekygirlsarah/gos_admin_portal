from importlib import import_module

from django.apps import AppConfig


class TravelConfig(AppConfig):
    name = "travel"
    verbose_name = "Travel"

    def ready(self):
        import_module("travel.cleanup")
