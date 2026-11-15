from django.urls import path

from .views import OrganizationAuditLogView

app_name = "audit"

urlpatterns = [
    path("", OrganizationAuditLogView.as_view(), name="organization_log"),
]
