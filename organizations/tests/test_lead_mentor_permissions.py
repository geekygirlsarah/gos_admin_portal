"""Tests for Lead Mentor role permissions and multi-organization isolation.

Verifies that:
- Users are correctly granted/denied Lead Mentor access based on their org role
- Role separation across organizations is enforced
- Cross-organization data isolation works properly
- Unscoped records are handled correctly
"""

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, TestCase
from django.urls import reverse

from audit.events import AuditEvent
from audit.models import AuditLog
from organizations.models import Organization, UserOrganizationMembership
from programs.models import Adult
from programs.permission_views import (
    get_user_role,
    user_is_lead_mentor,
    user_is_mentor,
    user_is_mentor_or_lead,
    user_is_parent,
)

User = get_user_model()


class LeadMentorRolePermissionTests(TestCase):
    """Tests for Lead Mentor role detection and multi-organization role separation."""

    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")
        self.other_org = Organization.objects.create(
            name="Other Robotics", slug="other", subdomain="other"
        )
        self.momentum = Organization.objects.get_or_create(
            slug="momentum", defaults={"name": "Momentum", "subdomain": "momentum"}
        )[0]

        # Standard non-superuser, non-staff user
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            first_name="Test",
            last_name="User",
            is_staff=False,
            is_superuser=False,
        )
        self.momentum_user = User.objects.create_user(
            username="momentum_user",
            email="momentum@example.com",
            first_name="Momentum",
            last_name="User",
            is_staff=False,
            is_superuser=False,
        )
        # Grant org-level LeadMentor role in GoS
        self.membership = UserOrganizationMembership.objects.create(
            user=self.user,
            organization=self.gos,
            role="LeadMentor",
            is_active=True,
        )
        # Same user is only a Mentor in the other org
        UserOrganizationMembership.objects.create(
            user=self.user,
            organization=self.other_org,
            role="Mentor",
            is_active=True,
        )
        # Grant Mentor role in momentum
        self.momentum_membership = UserOrganizationMembership.objects.create(
            user=self.momentum_user,
            organization=self.momentum,
            role="Mentor",
            is_active=True,
        )

        # Regular mentor user (not lead mentor)
        self.mentor_user = User.objects.create_user(
            username="regular_mentor",
            email="mentor@example.com",
            first_name="Regular",
            last_name="Mentor",
            is_staff=False,
            is_superuser=False,
        )
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user,
            legal_first_name="Regular",
            last_name="Mentor",
            personal_email="mentor@example.com",
            is_mentor=True,
            organization=self.gos,
        )
        # Creating a mentor Adult already auto-creates this membership via signal.
        UserOrganizationMembership.objects.get_or_create(
            user=self.mentor_user,
            organization=self.gos,
            role="Mentor",
            defaults={"is_active": True},
        )

        # Superuser (system admin)
        self.admin_user = User.objects.create_superuser(
            username="sysadmin",
            email="admin@example.com",
            password="adminpassword123",  # nosec B106
        )

        self.client = Client()

    def test_org_lead_mentor_detected_by_helpers(self):
        """A user with UserOrganizationMembership(role='LeadMentor') is recognized as Lead Mentor."""
        self.assertEqual(get_user_role(self.user, org=self.gos), "LeadMentor")
        self.assertTrue(user_is_lead_mentor(self.user, org=self.gos))
        self.assertTrue(user_is_mentor_or_lead(self.user, org=self.gos))

        # But NOT a lead mentor in other_org
        self.assertEqual(get_user_role(self.user, org=self.other_org), "Mentor")
        self.assertFalse(user_is_lead_mentor(self.user, org=self.other_org))

        # Momentum user should not have GoS role
        self.assertIsNone(get_user_role(self.momentum_user, org=self.gos))

        # Momentum user should have Mentor role in momentum
        self.assertEqual(get_user_role(self.momentum_user, org=self.momentum), "Mentor")

    def test_multi_org_roles_for_same_user(self):
        """A user can hold different roles across different organizations."""
        self.assertEqual(get_user_role(self.user, org=self.gos), "LeadMentor")
        self.assertEqual(get_user_role(self.user, org=self.other_org), "Mentor")

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

    def test_get_user_role_with_org(self):
        """get_user_role respects org parameter and does not fall through to global flags."""
        # User with GoS membership should have GoS role when org=self.gos
        self.assertEqual(get_user_role(self.user, org=self.gos), "LeadMentor")
        # Same user with org=self.other_org should have other_org role
        self.assertEqual(get_user_role(self.user, org=self.other_org), "Mentor")
        # User with no membership in org should get None (not fall through to global)
        self.assertIsNone(get_user_role(self.user, org=self.momentum))

    def test_user_is_parent_and_mentor_with_org(self):
        """user_is_parent and user_is_mentor respect organization context."""
        UserOrganizationMembership.objects.create(
            user=self.user, organization=self.gos, role="Parent", is_active=True
        )
        UserOrganizationMembership.objects.create(
            user=self.user, organization=self.gos, role="Mentor", is_active=True
        )
        self.assertTrue(user_is_parent(self.user, org=self.gos))
        self.assertFalse(user_is_parent(self.user, org=self.other_org))
        self.assertTrue(user_is_mentor(self.user, org=self.gos))
        self.assertTrue(user_is_mentor(self.user, org=self.other_org))
        self.assertFalse(user_is_mentor(self.user, org=self.momentum))

    def test_momentum_user_no_gos_role(self):
        """A user with membership only in momentum should not be granted GoS role."""
        # setUp gives momentum_user a momentum membership but not gos
        # Should NOT return Mentor when checking against GoS
        self.assertIsNone(get_user_role(self.momentum_user, org=self.gos))
        # Should return Mentor when checking against momentum
        self.assertEqual(get_user_role(self.momentum_user, org=self.momentum), "Mentor")

    def test_for_organization_excludes_null_scoped(self):
        """for_organization should exclude records with organization__isnull=True."""
        from orders.models import Order

        # Create a record with no organization
        order_no_org = Order.objects.create(
            status="pending",
        )
        # for_organization(momentum) should not include it
        orders = Order.objects.for_organization(self.momentum)
        self.assertNotIn(order_no_org, orders)
        # But records with momentum org should be included
        order_with_momentum = Order.objects.create(
            status="pending",
            organization=self.momentum,
        )
        self.assertIn(order_with_momentum, orders)

    def test_travel_no_cross_org_records(self):
        """Travel views must not return records from other orgs."""
        from datetime import date

        from programs.models import Student
        from travel.models import TravelEvent, TravelSignup

        student = Student.objects.create(
            legal_first_name="T", last_name="S", organization=self.gos
        )
        event = TravelEvent.objects.create(
            name="Trip",
            start_date=date.today(),
            end_date=date.today(),
            organization=self.gos,
        )
        # Signup belonging to another org
        other_signup = TravelSignup.objects.create(
            student=student, event=event, organization=self.gos
        )
        # Momentum query should not include it
        signups = TravelSignup.objects.for_organization(self.momentum)
        self.assertNotIn(other_signup, signups)

    def test_momentum_no_gos_fallback(self):
        """A request resolving to Momentum must not fall back to GoS permissions."""
        # setUp gives momentum_user a momentum membership
        # When org=momentum is passed, should NOT fall back to GoS
        role = get_user_role(self.momentum_user, org=self.momentum)
        self.assertEqual(role, "Mentor")
        # Same user with org=self.gos should get None (no GoS membership)
        self.assertIsNone(get_user_role(self.momentum_user, org=self.gos))

    def test_orders_cross_org_isolation(self):
        """Orders views must not return records from other orgs."""
        from orders.models import Order

        # Create an order for GoS
        gos_order = Order.objects.create(
            status="pending",
            organization=self.gos,
        )
        # Create an order for momentum
        momentum_order = Order.objects.create(
            status="pending",
            organization=self.momentum,
        )
        # Momentum org should only see momentum orders
        momentum_orders = Order.objects.for_organization(self.momentum)
        self.assertIn(momentum_order, momentum_orders)
        self.assertNotIn(gos_order, momentum_orders)


class CrossOrgIsolationTests(TestCase):
    """Tests that ensure one organization cannot see another organization's data."""

    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")
        self.other_org = Organization.objects.create(
            name="Other Robotics", slug="other", subdomain="other"
        )
        self.momentum = Organization.objects.get_or_create(
            slug="momentum", defaults={"name": "Momentum", "subdomain": "momentum"}
        )[0]

        # GoS user
        self.gos_user = User.objects.create_user(
            username="gos_user",
            email="gos@example.com",
            first_name="GoS",
            last_name="User",
            is_staff=False,
            is_superuser=False,
        )
        UserOrganizationMembership.objects.create(
            user=self.gos_user,
            organization=self.gos,
            role="LeadMentor",
            is_active=True,
        )

        # Momentum user
        self.momentum_user = User.objects.create_user(
            username="momentum_user",
            email="momentum@example.com",
            first_name="Momentum",
            last_name="User",
            is_staff=False,
            is_superuser=False,
        )
        UserOrganizationMembership.objects.create(
            user=self.momentum_user,
            organization=self.momentum,
            role="Mentor",
            is_active=True,
        )

        self.client = Client()

    def test_users_cross_org_isolation(self):
        """A GoS user must not see Momentum users and vice versa."""
        # GoS user should not have momentum membership
        self.assertIsNone(get_user_role(self.gos_user, org=self.momentum))
        # Momentum user should not have GoS role
        self.assertIsNone(get_user_role(self.momentum_user, org=self.gos))

    def test_programs_cross_org_isolation(self):
        """Program views must not return programs from other orgs."""
        from programs.models import Program

        # Create programs for different orgs
        gos_program = Program.objects.create(
            name="GoS Program", organization=self.gos, active=True
        )
        other_program = Program.objects.create(
            name="Other Program", organization=self.other_org, active=True
        )
        momentum_program = Program.objects.create(
            name="Momentum Program", organization=self.momentum, active=True
        )

        # GoS query should only see GoS programs
        gos_programs = Program.objects.for_organization(self.gos)
        self.assertIn(gos_program, gos_programs)
        self.assertNotIn(other_program, gos_programs)
        self.assertNotIn(momentum_program, gos_programs)

        # Momentum query should only see Momentum programs
        momentum_programs = Program.objects.for_organization(self.momentum)
        self.assertIn(momentum_program, momentum_programs)
        self.assertNotIn(gos_program, momentum_programs)
        self.assertNotIn(other_program, momentum_programs)

    def test_students_cross_org_isolation(self):
        """Student views must not return students from other orgs."""
        from programs.models import Student

        # Create students for different orgs
        gos_student = Student.objects.create(
            legal_first_name="GoS",
            last_name="Student",
            organization=self.gos,
        )
        other_student = Student.objects.create(
            legal_first_name="Other",
            last_name="Student",
            organization=self.other_org,
        )
        momentum_student = Student.objects.create(
            legal_first_name="Momentum",
            last_name="Student",
            organization=self.momentum,
        )

        # GoS query should only see GoS students
        gos_students = Student.objects.for_organization(self.gos)
        self.assertIn(gos_student, gos_students)
        self.assertNotIn(other_student, gos_students)
        self.assertNotIn(momentum_student, gos_students)

    def test_adults_cross_org_isolation(self):
        """Adult views must not return adults from other orgs."""
        from programs.models import Adult

        # Create adults for different orgs
        gos_adult = Adult.objects.create(
            user=self.gos_user,
            legal_first_name="GoS",
            last_name="Adult",
            personal_email="gos@example.com",
            is_mentor=True,
            organization=self.gos,
        )
        other_adult = Adult.objects.create(
            user=None,
            legal_first_name="Other",
            last_name="Adult",
            personal_email="other@example.com",
            is_mentor=True,
            organization=self.other_org,
        )
        momentum_adult = Adult.objects.create(
            user=self.momentum_user,
            legal_first_name="Momentum",
            last_name="Adult",
            personal_email="momentum@example.com",
            is_mentor=True,
            organization=self.momentum,
        )

        # GoS query should only see GoS adults
        gos_adults = Adult.objects.for_organization(self.gos)
        self.assertIn(gos_adult, gos_adults)
        self.assertNotIn(other_adult, gos_adults)
        self.assertNotIn(momentum_adult, gos_adults)

    def test_badges_cross_org_isolation(self):
        """Badge views must not return badges from other orgs."""
        from badges.models import Badge

        # Create badges for different orgs
        gos_badge = Badge.objects.create(name="GoS Badge", organization=self.gos)
        other_badge = Badge.objects.create(
            name="Other Badge", organization=self.other_org
        )
        momentum_badge = Badge.objects.create(
            name="Momentum Badge", organization=self.momentum
        )

        # GoS query should only see GoS badges
        gos_badges = Badge.objects.for_organization(self.gos)
        self.assertIn(gos_badge, gos_badges)
        self.assertNotIn(other_badge, gos_badges)
        self.assertNotIn(momentum_badge, gos_badges)

    def test_orders_cross_org_isolation_detailed(self):
        """Orders views must not return orders from other orgs (detailed test)."""
        from orders.models import Order, OrderItem

        # Create orders for different orgs
        gos_order = Order.objects.create(status="pending", organization=self.gos)
        other_order = Order.objects.create(
            status="pending", organization=self.other_org
        )
        momentum_order = Order.objects.create(
            status="pending", organization=self.momentum
        )

        # GoS order should not appear in momentum query
        momentum_orders = Order.objects.for_organization(self.momentum)
        self.assertIn(momentum_order, momentum_orders)
        self.assertNotIn(gos_order, momentum_orders)
        self.assertNotIn(other_order, momentum_orders)

    def test_travel_cross_org_isolation_detailed(self):
        """Travel views must not return travel records from other orgs (detailed)."""
        from datetime import date

        from programs.models import Student
        from travel.models import TravelEvent, TravelSignup

        def make_signup(org):
            student = Student.objects.create(
                legal_first_name="T", last_name=org.slug, organization=org
            )
            event = TravelEvent.objects.create(
                name=f"Trip {org.slug}",
                start_date=date.today(),
                end_date=date.today(),
                organization=org,
            )
            return TravelSignup.objects.create(
                student=student, event=event, organization=org, status="approved"
            )

        gos_signup = make_signup(self.gos)
        other_signup = make_signup(self.other_org)
        momentum_signup = make_signup(self.momentum)

        # Momentum query should only see Momentum signups
        momentum_signups = TravelSignup.objects.for_organization(self.momentum)
        self.assertIn(momentum_signup, momentum_signups)
        self.assertNotIn(gos_signup, momentum_signups)
        self.assertNotIn(other_signup, momentum_signups)

    def test_applications_cross_org_isolation(self):
        """Application views must not return applications from other orgs."""
        from applications.models import Application

        # Create applications for different orgs
        gos_application = Application.objects.create(
            email="gos@example.com", organization=self.gos
        )
        other_application = Application.objects.create(
            email="other@example.com", organization=self.other_org
        )
        momentum_application = Application.objects.create(
            email="momentum@example.com", organization=self.momentum
        )

        # GoS query should only see GoS applications
        gos_apps = Application.objects.for_organization(self.gos)
        self.assertIn(gos_application, gos_apps)
        self.assertNotIn(other_application, gos_apps)
        self.assertNotIn(momentum_application, gos_apps)

    def test_finance_cross_org_isolation(self):
        """Financial records must not cross organizational boundaries."""
        from datetime import date

        from orders.models import Order

        # Create a GoS program and student
        from programs.models import Fee, Payment, Program, SlidingScale, Student

        gos_program = Program.objects.create(name="GoS Program", organization=self.gos)
        gos_student = Student.objects.create(
            legal_first_name="GoS",
            last_name="Student",
            organization=self.gos,
        )

        # GoS fee and payment
        gos_fee = Fee.objects.create(
            program=gos_program,
            name="GoS Fee",
            amount=100.00,
            organization=self.gos,
        )
        gos_payment = Payment.objects.create(
            program=gos_program,
            student=gos_student,
            amount=100.00,
            paid_on=date.today(),
            organization=self.gos,
        )

        # Other org fee and payment
        other_fee = Fee.objects.create(
            program=gos_program,
            name="Other Fee",
            amount=50.00,
            organization=self.other_org,
        )
        other_payment = Payment.objects.create(
            program=gos_program,
            student=gos_student,
            amount=50.00,
            paid_on=date.today(),
            organization=self.other_org,
        )

        # GoS query should only see GoS financial records
        gos_fees = Fee.objects.for_organization(self.gos)
        self.assertIn(gos_fee, gos_fees)
        self.assertNotIn(other_fee, gos_fees)

        gos_payments = Payment.objects.for_organization(self.gos)
        self.assertIn(gos_payment, gos_payments)
        self.assertNotIn(other_payment, gos_payments)

    def test_venue_cross_org_isolation(self):
        """Outreach location/venue records must be org-scoped."""
        from outreach.models import OutreachLocation

        # Create locations for different orgs
        gos_location = OutreachLocation.objects.create(
            name="GoS Library", address="123 GoS St", organization=self.gos
        )
        other_location = OutreachLocation.objects.create(
            name="Other Mall", address="456 Other St", organization=self.other_org
        )
        momentum_location = OutreachLocation.objects.create(
            name="Momentum Center",
            address="789 Momentum St",
            organization=self.momentum,
        )

        # GoS query should only see GoS locations
        gos_locations = OutreachLocation.objects.for_organization(self.gos)
        self.assertIn(gos_location, gos_locations)
        self.assertNotIn(other_location, gos_locations)
        self.assertNotIn(momentum_location, gos_locations)

    def test_kiosk_cross_org_isolation(self):
        """Kiosk configurations must be org-scoped."""
        from attendance.models import KioskConfig
        from programs.models import Program

        gos_program = Program.objects.create(name="GoS Program", organization=self.gos)

        # Create kiosks for different orgs
        gos_kiosk = KioskConfig.objects.create(
            label="GoS Kiosk 1", program=gos_program, organization=self.gos
        )
        other_kiosk = KioskConfig.objects.create(
            label="Other Kiosk", program=gos_program, organization=self.other_org
        )
        momentum_kiosk = KioskConfig.objects.create(
            label="Momentum Kiosk", program=gos_program, organization=self.momentum
        )

        # GoS query should only see GoS kiosks
        gos_kiosks = KioskConfig.objects.for_organization(self.gos)
        self.assertIn(gos_kiosk, gos_kiosks)
        self.assertNotIn(other_kiosk, gos_kiosks)
        self.assertNotIn(momentum_kiosk, gos_kiosks)
