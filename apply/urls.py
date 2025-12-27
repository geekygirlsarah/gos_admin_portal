from django.urls import path

from . import views

urlpatterns = [
    path("", views.apply_wizard, name="apply_start"),
    path("<str:step>/", views.apply_wizard, name="apply_step"),
]
