from django.urls import path

from .signout_views import digital_signout

urlpatterns = [
    path("<int:config_id>/", digital_signout, name="digital_signout"),
]
