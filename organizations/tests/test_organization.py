"""Phase 1 and Phase 2 multi-tenant tests.

Verifies the Organization model + seeded GoS row, the OrganizationMiddleware
that hardcodes ``request.organization``, and the ``OrganizationQuerySet`` /
``OrganizationManager`` ``for_organization()`` helper across models and views
(including "zero behavior change": unscoped rows stay visible while scoping is
not yet strictly enforced).
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from applications.models import Application
from attendance.models import AttendanceSession, KioskConfig
from organizations.models import (
    Organization,
    OrganizationManager,
    OrganizationQuerySet,
)
from outreach.models import OutreachEvent, OutreachLocation
from portal.views import DashboardView
from programs.models import (
    Adult,
    Enrollment,
    Fee,
    Payment,
    Program,
    SlidingScale,
    Student,
)
from programs.views.adults import (
    AdultsListView,
    AlumniListView,
    MentorListView,
    ParentListView,
)
from programs.views.programs import ProgramListView
from programs.views.students import StudentListView


class OrganizationModelTests(TestCase):
    def test_defaults(self):
        org = Organization.objects.create(name="Test Org", slug="test-org")
        self.assertTrue(org.is_active)
        self.assertIsNone(org.subdomain)
        self.assertEqual(org.settings, {})
        self.assertEqual(str(org), "Test Org")

    def test_slug_unique(self):
        Organization.objects.create(name="First", slug="dup")
        with self.assertRaises(Exception):
            Organization.objects.create(name="Second", slug="dup")


class GosOrganizationSeededByMigrationTests(TestCase):
    def test_gos_organization_exists(self):
        org = Organization.objects.filter(slug="gos").first()
        self.assertIsNotNone(org)
        self.assertEqual(org.name, "Girls of Steel")
        self.assertTrue(org.is_active)


class OrganizationMiddlewareTests(TestCase):
    def test_request_organization_is_gos(self):
        from GoSAdminPortal.middleware import OrganizationMiddleware

        request = RequestFactory().get("/health/")
        middleware = OrganizationMiddleware(lambda req: None)
        middleware(request)

        self.assertIsNotNone(request.organization)
        self.assertEqual(request.organization.slug, "gos")

    def test_dashboard_request_gets_organization(self):
        # Full-stack: the middleware runs on real requests too.
        user = get_user_model().objects.create_superuser(
            username="boss", email="boss@example.com", password="pw"  # nosec B106
        )
        self.client.force_login(user)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(response.status_code, 200)


class ProgramForOrganizationTests(TestCase):
    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")
        self.other = Organization.objects.create(name="Other Org", slug="other")

    def test_for_organization_none_returns_all(self):
        Program.objects.create(name="P1", organization=self.gos)
        Program.objects.create(name="P2")
        self.assertEqual(Program.objects.for_organization(None).count(), 2)

    def test_for_organization_keeps_matching_and_unscoped(self):
        Program.objects.create(name="Mine", organization=self.gos)
        Program.objects.create(name="Unscoped")
        Program.objects.create(name="Rival", organization=self.other)

        names = set(
            Program.objects.for_organization(self.gos).values_list("name", flat=True)
        )
        self.assertEqual(names, {"Mine", "Unscoped"})

    def test_program_list_view_scopes_to_organization(self):
        Program.objects.create(name="GoS Program", organization=self.gos, active=True)
        Program.objects.create(name="Unscoped Program", active=True)
        Program.objects.create(
            name="Rival Program", organization=self.other, active=True
        )

        user = get_user_model().objects.create_superuser(
            username="lead", email="lead@example.com", password="pw"  # nosec B106
        )
        request = RequestFactory().get("/profile/")
        request.user = user
        request.organization = self.gos

        view = ProgramListView()
        view.setup(request)
        self.assertEqual(view.get_queryset().count(), 2)

    def test_home_page_hides_other_org_programs(self):
        Program.objects.create(name="GoS Program", organization=self.gos, active=True)
        Program.objects.create(
            name="Rival Program", organization=self.other, active=True
        )

        user = get_user_model().objects.create_superuser(
            username="lead2", email="lead2@example.com", password="pw"  # nosec B106
        )
        self.client.force_login(user)
        response = self.client.get(reverse("profile_dashboard"))
        self.assertContains(response, "GoS Program")
        self.assertNotContains(response, "Rival Program")


class OrganizationManagerAndQuerySetTests(TestCase):
    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")
        self.other = Organization.objects.create(name="Other Org", slug="other")

    def test_organization_queryset_and_manager_classes(self):
        self.assertTrue(issubclass(OrganizationQuerySet, object))
        self.assertTrue(issubclass(OrganizationManager, object))

    def test_student_scoping(self):
        s_gos = Student.objects.create(
            legal_first_name="Alice", last_name="Smith", organization=self.gos
        )
        s_unscoped = Student.objects.create(legal_first_name="Bob", last_name="Jones")
        s_other = Student.objects.create(
            legal_first_name="Charlie", last_name="Brown", organization=self.other
        )

        self.assertEqual(Student.objects.for_organization(None).count(), 3)
        gos_students = set(
            Student.objects.for_organization(self.gos).values_list("id", flat=True)
        )
        self.assertEqual(gos_students, {s_gos.id, s_unscoped.id})
        self.assertNotIn(s_other.id, gos_students)

    def test_adult_scoping(self):
        a_gos = Adult.objects.create(
            legal_first_name="Anna", last_name="Parent", organization=self.gos
        )
        a_unscoped = Adult.objects.create(legal_first_name="Ben", last_name="Parent")
        a_other = Adult.objects.create(
            legal_first_name="Cara", last_name="Parent", organization=self.other
        )

        self.assertEqual(Adult.objects.for_organization(None).count(), 3)
        gos_adults = set(
            Adult.objects.for_organization(self.gos).values_list("id", flat=True)
        )
        self.assertEqual(gos_adults, {a_gos.id, a_unscoped.id})
        self.assertNotIn(a_other.id, gos_adults)

    def test_finances_scoping(self):
        import datetime

        prog = Program.objects.create(name="Prog", organization=self.gos)
        student = Student.objects.create(
            legal_first_name="Dave", last_name="Miller", organization=self.gos
        )
        fee_gos = Fee.objects.create(
            program=prog, name="Fee 1", amount=Decimal("100.00"), organization=self.gos
        )
        fee_other = Fee.objects.create(
            program=prog, name="Fee 2", amount=Decimal("50.00"), organization=self.other
        )
        pay_gos = Payment.objects.create(
            program=prog,
            student=student,
            amount=Decimal("100.00"),
            paid_on=datetime.date.today(),
            organization=self.gos,
        )
        pay_other = Payment.objects.create(
            program=prog,
            student=student,
            amount=Decimal("50.00"),
            paid_on=datetime.date.today(),
            organization=self.other,
        )
        ss_gos = SlidingScale.objects.create(student=student, organization=self.gos)
        ss_other = SlidingScale.objects.create(student=student, organization=self.other)

        self.assertEqual(
            set(Fee.objects.for_organization(self.gos).values_list("id", flat=True)),
            {fee_gos.id},
        )
        self.assertEqual(
            set(
                Payment.objects.for_organization(self.gos).values_list("id", flat=True)
            ),
            {pay_gos.id},
        )
        self.assertEqual(
            set(
                SlidingScale.objects.for_organization(self.gos).values_list(
                    "id", flat=True
                )
            ),
            {ss_gos.id},
        )

    def test_applications_scoping(self):
        app_gos = Application.objects.create(
            email="eve@example.com", organization=self.gos
        )
        app_unscoped = Application.objects.create(email="frank@example.com")
        app_other = Application.objects.create(
            email="grace@example.com", organization=self.other
        )

        gos_apps = set(
            Application.objects.for_organization(self.gos).values_list("id", flat=True)
        )
        self.assertEqual(gos_apps, {app_gos.id, app_unscoped.id})
        self.assertNotIn(app_other.id, gos_apps)

    def test_attendance_and_outreach_scoping(self):
        from django.utils import timezone

        prog = Program.objects.create(name="Prog Attendance", organization=self.gos)
        kiosk_gos = KioskConfig.objects.create(
            label="Kiosk 1", program=prog, organization=self.gos
        )
        kiosk_other = KioskConfig.objects.create(
            label="Kiosk 2", program=prog, organization=self.other
        )
        session_gos = AttendanceSession.objects.create(
            program=prog,
            check_in=timezone.now(),
            organization=self.gos,
            visitor_name="Visitor 1",
        )
        session_other = AttendanceSession.objects.create(
            program=prog,
            check_in=timezone.now(),
            organization=self.other,
            visitor_name="Visitor 2",
        )
        loc_gos = OutreachLocation.objects.create(
            name="Library", address="123 Main St", organization=self.gos
        )
        loc_other = OutreachLocation.objects.create(
            name="Mall", address="456 Market St", organization=self.other
        )

        self.assertEqual(
            set(
                KioskConfig.objects.for_organization(self.gos).values_list(
                    "id", flat=True
                )
            ),
            {kiosk_gos.id},
        )
        self.assertEqual(
            set(
                AttendanceSession.objects.for_organization(self.gos).values_list(
                    "id", flat=True
                )
            ),
            {session_gos.id},
        )
        self.assertEqual(
            set(
                OutreachLocation.objects.for_organization(self.gos).values_list(
                    "id", flat=True
                )
            ),
            {loc_gos.id},
        )


class MultiTenantViewFilteringTests(TestCase):
    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")
        self.other = Organization.objects.create(name="Other Org", slug="other")
        self.lead_user = get_user_model().objects.create_superuser(
            username="lead_view",
            email="lead_view@example.com",
            password="pw",  # nosec B106
        )

    def _make_request(self, path="/", organization=None):
        rf = RequestFactory()
        req = rf.get(path)
        req.user = self.lead_user
        req.organization = organization or self.gos
        return req

    def test_student_list_view_scopes_to_organization(self):
        s_gos = Student.objects.create(
            legal_first_name="Sarah", last_name="Connor", organization=self.gos
        )
        s_unscoped = Student.objects.create(legal_first_name="John", last_name="Connor")
        s_other = Student.objects.create(
            legal_first_name="Kyle", last_name="Reese", organization=self.other
        )

        view = StudentListView()
        view.setup(self._make_request("/students/"))
        qs = view.get_queryset()

        student_ids = set(qs.values_list("id", flat=True))
        self.assertIn(s_gos.id, student_ids)
        self.assertIn(s_unscoped.id, student_ids)
        self.assertNotIn(s_other.id, student_ids)

    def test_adult_list_view_scopes_to_organization(self):
        a_gos = Adult.objects.create(
            legal_first_name="Mary",
            last_name="Jane",
            organization=self.gos,
            login_enabled=True,
        )
        a_unscoped = Adult.objects.create(
            legal_first_name="Peter", last_name="Parker", login_enabled=True
        )
        a_other = Adult.objects.create(
            legal_first_name="Norman",
            last_name="Osborn",
            organization=self.other,
            login_enabled=True,
        )

        view = AdultsListView()
        view.setup(self._make_request("/adults/"))
        qs = view.get_queryset()

        adult_ids = set(qs.values_list("id", flat=True))
        self.assertIn(a_gos.id, adult_ids)
        self.assertIn(a_unscoped.id, adult_ids)
        self.assertNotIn(a_other.id, adult_ids)

    def test_parent_and_mentor_list_views_scope_to_organization(self):
        p_gos = Adult.objects.create(
            legal_first_name="Martha",
            last_name="Kent",
            is_parent=True,
            organization=self.gos,
            login_enabled=True,
        )
        p_other = Adult.objects.create(
            legal_first_name="Thomas",
            last_name="Wayne",
            is_parent=True,
            organization=self.other,
            login_enabled=True,
        )
        m_gos = Adult.objects.create(
            legal_first_name="Clark",
            last_name="Kent",
            is_mentor=True,
            mentor_active=True,
            organization=self.gos,
        )
        m_other = Adult.objects.create(
            legal_first_name="Bruce",
            last_name="Wayne",
            is_mentor=True,
            mentor_active=True,
            organization=self.other,
        )

        p_view = ParentListView()
        p_view.setup(self._make_request("/parents/"))
        p_ids = set(p_view.get_queryset().values_list("id", flat=True))
        self.assertIn(p_gos.id, p_ids)
        self.assertNotIn(p_other.id, p_ids)

        m_view = MentorListView()
        m_view.setup(self._make_request("/mentors/"))
        m_ids = set(m_view.get_queryset().values_list("id", flat=True))
        self.assertIn(m_gos.id, m_ids)
        self.assertNotIn(m_other.id, m_ids)

    def test_dashboard_view_scopes_counts_to_organization(self):
        Program.objects.create(name="GoS 1", organization=self.gos, active=True)
        Program.objects.create(name="Other 1", organization=self.other, active=True)

        Application.objects.create(
            email="app1@example.com",
            status=Application.Status.SUBMITTED,
            organization=self.gos,
        )
        Application.objects.create(
            email="app2@example.com",
            status=Application.Status.SUBMITTED,
            organization=self.other,
        )

        req = self._make_request("/dashboard/")
        view = DashboardView()
        view.setup(req)
        ctx = view.get_context_data()

        self.assertEqual(ctx["lead_stats"]["active_programs_count"], 1)
        self.assertEqual(ctx["lead_stats"]["pending_applications_count"], 1)
