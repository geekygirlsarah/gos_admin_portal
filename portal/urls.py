from django.urls import path

from .views import DashboardView, MyProfileView

urlpatterns = [
    path("", DashboardView.as_view(), name="profile_dashboard"),
    path("my-profile/", MyProfileView.as_view(), name="my_profile"),
]
