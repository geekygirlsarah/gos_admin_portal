from django.urls import path

from .views import DashboardView, MyProfileView, ParentPaymentsView

urlpatterns = [
    path("", DashboardView.as_view(), name="profile_dashboard"),
    path("my-profile/", MyProfileView.as_view(), name="my_profile"),
    path("payments/", ParentPaymentsView.as_view(), name="parent_payments"),
]
