from django.urls import include, path

from . import kiosk_views

kiosk_patterns = [
    path(
        "request_code/", kiosk_views.kiosk_request_code, name="api_kiosk_request_code"
    ),
    path("unlock/", kiosk_views.kiosk_unlock, name="api_kiosk_unlock"),
    path("lock/", kiosk_views.kiosk_lock, name="api_kiosk_lock"),
    path("tap/", kiosk_views.kiosk_tap, name="api_kiosk_tap"),
    path("lookup/", kiosk_views.kiosk_lookup, name="api_kiosk_lookup"),
    path("manifest/", kiosk_views.kiosk_manifest, name="api_kiosk_manifest"),
]

urlpatterns = [
    path("kiosk/<int:kiosk_id>/", include(kiosk_patterns)),
]
