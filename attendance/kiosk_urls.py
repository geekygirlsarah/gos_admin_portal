from django.urls import path

from .kiosk_views import kiosk_index, kiosk_signin

urlpatterns = [
    path("", kiosk_index, name="kiosk_index"),
    path("<int:kiosk_id>/", kiosk_signin, name="kiosk_signin"),
]
