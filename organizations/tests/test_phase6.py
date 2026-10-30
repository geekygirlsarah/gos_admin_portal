"""Phase 6 tests: Auth / Onboarding Prep & Multi-Tenant Organization Resolution.

Verifies:
1. UserOrganizationMembership model (user <-> org <-> role) and scoping.
2. OrganizationInvitation model (token generation, expiration, acceptance).
3. Subdomain, header, and domain resolution in OrganizationMiddleware.
4. Org-scoped get_user_role() using UserOrganizationMembership.
5. Invitation acceptance view workflow.
6. Momentum domain defaults and URL resolution.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from organizations.models import (
    Organization,
    OrganizationInvitation,
    UserOrganizationMembership,
)
from organizations.utils import (
    get_organization_url,
    resolve_organization_from_request,
)
from programs.models import Adult, Student
from programs.permission_views import (
    can_user_read,
    can_user_write,
    get_user_role,
    user_is_mentor,
    user_is_mentor_or_lead,
    user_is_parent,
)


class UserOrganizationMembershipTests(TestCase):
    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")
        self.other_org = Organization.objects.create(
            name="Momentum Robotics", slug="momentum", subdomain="momentum"
        )
        User = get_user_model()
        self.user = User.objects.create_user(
            username="sarah_test",
            email="sarah@example.com",
            password="password123",  # nosec B106
        )

    def test_membership_creation_and_str(self):
        """UserOrganizationMembership connects a user to an organization with a role."""
        membership = UserOrganizationMembership.objects.create(
            user=self.user,
            organization=self.gos,
            role="Mentor",
        )
        self.assertTrue(membership.is_active)
        self.assertEqual(membership.role, "Mentor")
        self.assertIn("sarah_test", str(membership))
        self.assertIn("Girls of Steel", str(membership))
        self.assertIn("Mentor", str(membership))

    def test_multi_org_roles_for_same_user(self):
        """A user can hold different roles across different organizations."""
        m_gos = UserOrganizationMembership.objects.create(
            user=self.user,
            organization=self.gos,
            role="Mentor",
        )
        m_other = UserOrganizationMembership.objects.create(
            user=self.user,
            organization=self.other_org,
            role="LeadMentor",
        )
        self.assertEqual(get_user_role(self.user, org=self.gos), "Mentor")
        self.assertEqual(get_user_role(self.user, org=self.other_org), "LeadMentor")

    def test_for_organization_scoping(self):
        """UserOrganizationMembership queryset supports for_organization scoping."""
        m_gos = UserOrganizationMembership.objects.create(
            user=self.user,
            organization=self.gos,
            role="Parent",
        )
        m_other = UserOrganizationMembership.objects.create(
            user=self.user,
            organization=self.other_org,
            role="Student",
        )
        gos_memberships = set(
            UserOrganizationMembership.objects.for_organization(self.gos)
        )
        self.assertIn(m_gos, gos_memberships)
        self.assertNotIn(m_other, gos_memberships)


class OrganizationInvitationTests(TestCase):
    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")
        self.other_org = Organization.objects.create(
            name="Momentum Robotics", slug="momentum", subdomain="momentum"
        )
        User = get_user_model()
        self.inviter = User.objects.create_user(
            username="inviter",
            email="lead@example.com",
            password="password123",  # nosec B106
        )
        self.invitee = User.objects.create_user(
            username="invitee",
            email="newmember@example.com",
            password="password123",  # nosec B106
        )

    def test_invitation_auto_token_and_expiration(self):
        """Creating an invitation auto-generates a secure token and sets expires_at."""
        invitation = OrganizationInvitation.objects.create(
            organization=self.other_org,
            email="newmember@example.com",
            role="Mentor",
            invited_by=self.inviter,
        )
        self.assertTrue(invitation.token)
        self.assertEqual(len(invitation.token), 43)  # 32 bytes urlsafe = 43 chars
        self.assertTrue(invitation.is_valid())
        self.assertEqual(invitation.status, OrganizationInvitation.STATUS_PENDING)

    def test_accept_invitation_creates_membership(self):
        """Accepting an invitation marks it accepted and creates UserOrganizationMembership."""
        invitation = OrganizationInvitation.objects.create(
            organization=self.other_org,
            email="newmember@example.com",
            role="LeadMentor",
            invited_by=self.inviter,
        )
        membership = invitation.accept(self.invitee)
        self.assertEqual(invitation.status, OrganizationInvitation.STATUS_ACCEPTED)
        self.assertEqual(invitation.accepted_by, self.invitee)
        self.assertIsNotNone(invitation.accepted_at)

        self.assertEqual(membership.user, self.invitee)
        self.assertEqual(membership.organization, self.other_org)
        self.assertEqual(membership.role, "LeadMentor")
        self.assertTrue(membership.is_active)

    def test_expired_invitation_cannot_be_accepted(self):
        """Expired invitations are invalid and reject acceptance."""
        invitation = OrganizationInvitation.objects.create(
            organization=self.other_org,
            email="newmember@example.com",
            role="Student",
            expires_at=timezone.now() - timedelta(days=1),
        )
        self.assertFalse(invitation.is_valid())
        with self.assertRaises(ValueError):
            invitation.accept(self.invitee)


class OrganizationResolutionMiddlewareTests(TestCase):
    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")
        self.momentum = Organization.objects.create(
            name="Momentum Robotics", slug="momentum", subdomain="momentum"
        )
        self.rf = RequestFactory()

    def test_resolve_from_header(self):
        """Header X-Organization resolves the organization by slug or subdomain."""
        request = self.rf.get("/", HTTP_X_ORGANIZATION="momentum")
        org = resolve_organization_from_request(request)
        self.assertEqual(org, self.momentum)

        request_gos = self.rf.get("/", HTTP_X_ORG_SLUG="gos")
        org_gos = resolve_organization_from_request(request_gos)
        self.assertEqual(org_gos, self.gos)

    def test_resolve_from_subdomain_momentumteam_app(self):
        """Host 'momentum.momentumteam.app' resolves to the momentum organization."""
        request = self.rf.get("/", HTTP_HOST="momentum.momentumteam.app")
        org = resolve_organization_from_request(request)
        self.assertEqual(org, self.momentum)

    def test_resolve_from_subdomain_localhost(self):
        """Host 'momentum.localhost:8000' resolves to the momentum organization."""
        request = self.rf.get("/", HTTP_HOST="momentum.localhost:8000")
        org = resolve_organization_from_request(request)
        self.assertEqual(org, self.momentum)

    def test_resolve_root_domain_returns_none_for_marketing(self):
        """Root domains ('momentumteam.app', 'www.momentumteam.app', 'localhost:8000') resolve to None (marketing site)."""
        request = self.rf.get("/", HTTP_HOST="momentumteam.app")
        org = resolve_organization_from_request(request)
        self.assertIsNone(org)

        request_www = self.rf.get("/", HTTP_HOST="www.momentumteam.app")
        org_www = resolve_organization_from_request(request_www)
        self.assertIsNone(org_www)

        request_local = self.rf.get("/", HTTP_HOST="localhost:8000")
        org_local = resolve_organization_from_request(request_local)
        self.assertIsNone(org_local)

    def test_resolve_gos_subdomain_and_cname(self):
        """'gos.momentumteam.app' and 'portal.girlsofsteelrobotics.org' resolve to gos."""
        request_sub = self.rf.get("/", HTTP_HOST="gos.momentumteam.app")
        org_sub = resolve_organization_from_request(request_sub)
        self.assertEqual(org_sub, self.gos)

        request_cname = self.rf.get("/", HTTP_HOST="portal.girlsofsteelrobotics.org")
        org_cname = resolve_organization_from_request(request_cname)
        self.assertEqual(org_cname, self.gos)

    def test_resolve_fallback_when_no_request(self):
        """When request is None, falls back to default organization."""
        org = resolve_organization_from_request(None)
        self.assertEqual(org, self.gos)

    def test_organization_middleware_attaches_request_organization(self):
        """OrganizationMiddleware dynamically resolves and attaches request.organization."""
        from GoSAdminPortal.middleware import OrganizationMiddleware

        middleware = OrganizationMiddleware(lambda req: None)

        req_momentum = self.rf.get("/", HTTP_HOST="momentum.momentumteam.app")
        middleware(req_momentum)
        self.assertEqual(req_momentum.organization, self.momentum)

        req_gos_local = self.rf.get("/", HTTP_HOST="gos.localhost:8000")
        middleware(req_gos_local)
        self.assertEqual(req_gos_local.organization, self.gos)

        req_root = self.rf.get("/", HTTP_HOST="localhost:8000")
        middleware(req_root)
        self.assertIsNone(req_root.organization)


class OrgScopedUserRoleTests(TestCase):
    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")
        self.other_org = Organization.objects.create(
            name="Momentum Team", slug="momentum", subdomain="momentum"
        )
        User = get_user_model()
        self.user = User.objects.create_user(
            username="multi_role_user",
            email="multi@example.com",
            password="password123",  # nosec B106
        )

        # In GoS: Parent
        UserOrganizationMembership.objects.create(
            user=self.user, organization=self.gos, role="Parent"
        )
        # In Momentum: LeadMentor
        UserOrganizationMembership.objects.create(
            user=self.user, organization=self.other_org, role="LeadMentor"
        )

    def test_get_user_role_with_org(self):
        """get_user_role returns the role scoped to the given organization."""
        self.assertEqual(get_user_role(self.user, org=self.gos), "Parent")
        self.assertEqual(get_user_role(self.user, org=self.other_org), "LeadMentor")

    def test_user_is_parent_and_mentor_with_org(self):
        """user_is_parent, user_is_mentor, and user_is_mentor_or_lead respect organization context."""
        self.assertTrue(user_is_parent(self.user, org=self.gos))
        self.assertFalse(user_is_mentor(self.user, org=self.gos))
        self.assertFalse(user_is_mentor_or_lead(self.user, org=self.gos))

        self.assertFalse(user_is_parent(self.user, org=self.other_org))
        self.assertTrue(user_is_mentor_or_lead(self.user, org=self.other_org))

        # Add mentor role in other_org and verify user_is_mentor
        UserOrganizationMembership.objects.create(
            user=self.user, organization=self.other_org, role="Mentor"
        )
        self.assertTrue(user_is_mentor(self.user, org=self.other_org))


class InvitationViewTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name="Momentum Team", slug="momentum", subdomain="momentum"
        )
        User = get_user_model()
        self.inviter = User.objects.create_user(
            username="inviter",
            email="lead@example.com",
            password="password123",  # nosec B106
        )
        self.user = User.objects.create_user(
            username="invited_user",
            email="member@example.com",
            password="password123",  # nosec B106
        )
        self.invitation = OrganizationInvitation.objects.create(
            organization=self.org,
            email="member@example.com",
            role="Mentor",
            invited_by=self.inviter,
        )

    def test_accept_invitation_view_authenticated(self):
        """Logged-in user visiting invitation link accepts invite and joins org."""
        self.client.force_login(self.user)
        url = reverse(
            "organizations:accept_invitation", kwargs={"token": self.invitation.token}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # POST to accept
        response = self.client.post(url)
        self.assertRedirects(response, reverse("profile_dashboard"))

        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.status, OrganizationInvitation.STATUS_ACCEPTED)
        self.assertTrue(
            UserOrganizationMembership.objects.filter(
                user=self.user, organization=self.org, role="Mentor"
            ).exists()
        )

    def test_accept_invalid_invitation_returns_404_or_error(self):
        """Visiting an invalid token triggers handler404 redirect to home or 404 status."""
        url = reverse(
            "organizations:accept_invitation",
            kwargs={"token": "invalid-token"},  # nosec B105
        )
        response = self.client.get(url)
        self.assertIn(response.status_code, (302, 404))


class MomentumDomainUtilsTests(TestCase):
    def test_get_organization_url_momentum_default(self):
        """get_organization_url returns subdomain.root_domain based on MOMENTUM_ROOT_DOMAIN."""
        org = Organization.objects.create(
            name="RoboBuilders", slug="robobuilders", subdomain="robobuilders"
        )
        url = get_organization_url(org)
        self.assertEqual(url, f"https://robobuilders.{settings.MOMENTUM_ROOT_DOMAIN}")

    @override_settings(MOMENTUM_ROOT_DOMAIN="momentumteam.app")
    def test_get_organization_url_momentum_prod_domain(self):
        """get_organization_url returns subdomain.momentumteam.app when configured."""
        org = Organization.objects.create(
            name="RoboBuilders", slug="robobuilders", subdomain="robobuilders"
        )
        url = get_organization_url(org)
        self.assertEqual(url, "https://robobuilders.momentumteam.app")
