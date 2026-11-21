from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase

from travel.admin import (
    TravelCostOptionAdmin,
    TravelCostOptionInline,
    TravelDepartureAdmin,
    TravelDepartureInline,
    TravelEventAdmin,
    TravelMentorSignupAdmin,
    TravelMentorSignupInline,
    TravelParentChaperoneSignupAdmin,
    TravelParentChaperoneSignupInline,
    TravelSignupAdmin,
    TravelSignupInline,
)
from travel.models import (
    TravelCostOption,
    TravelDeparture,
    TravelEvent,
    TravelMentorSignup,
    TravelParentChaperoneSignup,
    TravelSignup,
)


class TravelAdminPermissionTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admins = (
            TravelEventAdmin(TravelEvent, self.site),
            TravelDepartureAdmin(TravelDeparture, self.site),
            TravelCostOptionAdmin(TravelCostOption, self.site),
            TravelSignupAdmin(TravelSignup, self.site),
            TravelMentorSignupAdmin(TravelMentorSignup, self.site),
            TravelParentChaperoneSignupAdmin(TravelParentChaperoneSignup, self.site),
        )
        self.inlines = (
            TravelDepartureInline(TravelEvent, self.site),
            TravelCostOptionInline(TravelEvent, self.site),
            TravelSignupInline(TravelEvent, self.site),
            TravelMentorSignupInline(TravelEvent, self.site),
            TravelParentChaperoneSignupInline(TravelEvent, self.site),
        )
        self.factory = RequestFactory()
        self.platform_admin = User.objects.create_superuser(
            username="travel-platform-admin",
            email="travel-platform-admin@example.com",
            password="password",
        )  # nosec B106
        self.tenant_admin = User.objects.create_superuser(
            username="travel-tenant-admin",
            email="travel-tenant-admin@example.com",
            password="password",
        )  # nosec B106
        self.staff_user = User.objects.create_user(
            username="travel-staff",
            email="travel-staff@example.com",
            password="password",
            is_staff=True,
        )  # nosec B106

    def _request(self, user, *, tenant=False):
        headers = {"HTTP_X_ORGANIZATION": "tenant"} if tenant else {}
        request = self.factory.get("/admin/", **headers)
        request.user = user
        if tenant:
            request.organization = object()
        return request

    def test_platform_superuser_can_view_but_not_mutate_travel_admin(self):
        request = self._request(self.platform_admin)

        for model_admin in self.admins:
            with self.subTest(admin=type(model_admin).__name__):
                self.assertTrue(model_admin.has_module_permission(request))
                self.assertTrue(model_admin.has_view_permission(request))
                self.assertFalse(model_admin.has_add_permission(request))
                self.assertFalse(model_admin.has_change_permission(request))
                self.assertFalse(model_admin.has_delete_permission(request))

        for inline in self.inlines:
            with self.subTest(inline=type(inline).__name__):
                self.assertTrue(inline.has_view_permission(request))
                self.assertFalse(inline.has_add_permission(request, None))
                self.assertFalse(inline.has_change_permission(request, None))
                self.assertFalse(inline.has_delete_permission(request, None))

    def test_non_superusers_and_tenant_superuser_cannot_access_travel_admin(self):
        denied_requests = (
            self._request(self.staff_user),
            self._request(self.tenant_admin, tenant=True),
        )

        for request in denied_requests:
            for model_admin in self.admins:
                with self.subTest(admin=type(model_admin).__name__):
                    self.assertFalse(model_admin.has_module_permission(request))
                    self.assertFalse(model_admin.has_view_permission(request))

            for inline in self.inlines:
                with self.subTest(inline=type(inline).__name__):
                    self.assertFalse(inline.has_view_permission(request))
