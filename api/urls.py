from django.urls import include, path

from . import kiosk_views, signout_views

kiosk_patterns = [
    path("ping/", kiosk_views.kiosk_ping, name="api_kiosk_ping"),
    path(
        "request_code/", kiosk_views.kiosk_request_code, name="api_kiosk_request_code"
    ),
    path("unlock/", kiosk_views.kiosk_unlock, name="api_kiosk_unlock"),
    path("lock/", kiosk_views.kiosk_lock, name="api_kiosk_lock"),
    path("tap/", kiosk_views.kiosk_tap, name="api_kiosk_tap"),
    path("lookup/", kiosk_views.kiosk_lookup, name="api_kiosk_lookup"),
    path("who-is-here/", kiosk_views.kiosk_who_is_here, name="api_kiosk_who_is_here"),
]

signout_patterns = [
    path(
        "request_code/",
        signout_views.signout_request_code,
        name="api_signout_request_code",
    ),
    path("unlock/", signout_views.signout_unlock, name="api_signout_unlock"),
    path("lock/", signout_views.signout_lock, name="api_signout_lock"),
]

urlpatterns = [
    path("kiosk/<int:kiosk_id>/", include(kiosk_patterns)),
    path("signout/<int:config_id>/", include(signout_patterns)),
]
