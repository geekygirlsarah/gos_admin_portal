import re
from datetime import timedelta
from decimal import Decimal
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.query import QuerySet
from django.db.models.signals import post_init
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from organizations.models import Organization, UserOrganizationMembership
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Program,
    ProgramFeature,
    School,
    Student,
)
from travel.forms import (
    TravelMentorStaffSignupForm,
    TravelParentChaperoneStaffForm,
    TravelSignupDepartureForm,
    TravelSignupForm,
    TravelStaffSignupForm,
)
from travel.models import (
    TravelCostOption,
    TravelDeparture,
    TravelEvent,
    TravelMentorSignup,
    TravelParentChaperoneSignup,
    TravelSignup,
)


class TravelViewTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.feature, _ = ProgramFeature.objects.get_or_create(
            key="travel", defaults={"name": "Travel"}
        )
        self.program = Program.objects.create(name="Test Program")
        self.program.features.add(self.feature)
        self.other_program = Program.objects.create(name="No Travel Program")

        self.organization = Organization.objects.get(slug="gos")
        self.lead = User.objects.create_user(
            username="lead", email="lead@example.com", password="password"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=self.lead,
            organization=self.organization,
            role="LeadMentor",
            is_active=True,
        )

        self.mentor_user = User.objects.create_user(
            username="mentor", password="password"
        )  # nosec B106
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user,
            is_mentor=True,
            mentor_active=True,
            login_enabled=True,
            email_updates=True,
            personal_email="mentor@example.com",
        )

        self.student_user = User.objects.create_user(
            username="student", email="student@example.com", password="password"
        )  # nosec B106
        self.student = Student.objects.create(
            user=self.student_user,
            legal_first_name="Test",
            last_name="Student",
            school=self.school,
            graduation_year=2027,
        )
        Enrollment.objects.create(
            student=self.student, program=self.program, active=True
        )

        self.parent_user = User.objects.create_user(
            username="parent", password="password"
        )  # nosec B106
        self.parent_adult = Adult.objects.create(
            user=self.parent_user,
            is_parent=True,
            login_enabled=True,
            email_updates=True,
            personal_email="parent@example.com",
        )
        AdultStudentRelationship.objects.create(
            adult=self.parent_adult, student=self.student
        )

        self.event = TravelEvent.objects.create(
            program=self.program,
            name="Competition Trip",
            start_date=timezone.now().date() + timedelta(days=30),
            end_date=timezone.now().date() + timedelta(days=34),
        )
        self.departure = TravelDeparture.objects.create(
            event=self.event,
            label="Bus 1",
            leave_date=timezone.now().date() + timedelta(days=30),
        )
        self.hotel = TravelCostOption.objects.create(
            event=self.event, name="Hotel", amount="300.00"
        )
        self.van = TravelCostOption.objects.create(
            event=self.event, name="Van", amount="50.00", is_required=False
        )

    def tearDown(self):
        mail.outbox = []

    def event_edit_payload(self):
        return {
            "name": self.event.name,
            "start_date": self.event.start_date.isoformat(),
            "end_date": self.event.end_date.isoformat(),
            "location_name": self.event.location_name,
            "location_address": self.event.location_address,
            "description": self.event.description,
            "departures-TOTAL_FORMS": "1",
            "departures-INITIAL_FORMS": "1",
            "departures-MIN_NUM_FORMS": "0",
            "departures-MAX_NUM_FORMS": "1000",
            "departures-0-id": str(self.departure.pk),
            "departures-0-label": self.departure.label,
            "departures-0-leave_date": self.departure.leave_date.isoformat(),
            "departures-0-leave_time": "",
            "departures-0-meeting_location": self.departure.meeting_location,
            "departures-0-notes": self.departure.notes,
            "costs-TOTAL_FORMS": "2",
            "costs-INITIAL_FORMS": "2",
            "costs-MIN_NUM_FORMS": "0",
            "costs-MAX_NUM_FORMS": "1000",
            "costs-0-id": str(self.hotel.pk),
            "costs-0-name": self.hotel.name,
            "costs-0-amount": str(self.hotel.amount),
            "costs-0-unit_label": self.hotel.unit_label,
            "costs-0-notes": self.hotel.notes,
            "costs-0-sort_order": str(self.hotel.sort_order),
            "costs-0-is_required": "on" if self.hotel.is_required else "",
            "costs-1-id": str(self.van.pk),
            "costs-1-name": self.van.name,
            "costs-1-amount": str(self.van.amount),
            "costs-1-unit_label": self.van.unit_label,
            "costs-1-notes": self.van.notes,
            "costs-1-sort_order": str(self.van.sort_order),
            "costs-1-is_required": "on" if self.van.is_required else "",
        }

    # ── Feature gating ────────────────────────────────────────────────────

    def test_list_redirects_for_program_without_travel_feature(self):
        self.client.login(username="student", password="password")  # nosec B106
        url = reverse("travel:event_list", args=[self.other_program.id])
        resp = self.client.get(url)
        # The custom handler404 redirects to home with a message.
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))

    def test_mentors_can_access_travel_pages_without_feature(self):
        for username in ("mentor", "lead"):
            self.client.login(username=username, password="password")  # nosec B106
            url = reverse("travel:event_list", args=[self.other_program.id])
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, msg=username)

    def test_create_form_offers_add_another_departure_and_cost(self):
        self.client.login(username="lead", password="password")  # nosec B106
        url = reverse("travel:event_create", args=[self.program.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("Add Another Departure", html)
        self.assertIn("Add Another Cost", html)
        self.assertIn("empty-departure-form-template", html)
        self.assertIn("empty-cost-form-template", html)
        self.assertIn("id_departures-TOTAL_FORMS", html)
        self.assertIn("id_costs-TOTAL_FORMS", html)
        self.assertIn("id_requires_departure", html)
        self.assertEqual(resp.context["cost_formset"].total_form_count(), 1)
        self.assertIn('class="form-control"', html)
        self.assertIn("Trip details", html)

    def test_mentor_sees_trips_read_only(self):
        signup = TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            departure=self.departure,
            status=TravelSignup.APPROVED,
            approved_total="350.00",
            permission_acknowledged=True,
        )
        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse("travel:event_list", args=[self.program.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["can_manage"])
        self.assertFalse(resp.context["can_add"])
        self.assertFalse(resp.context["can_edit"])
        html = resp.content.decode()
        self.assertNotIn(">Edit</a>", html)
        self.assertNotIn(">Delete</a>", html)
        self.assertNotIn("Add Trip", html)
        self.assertIn("Student Sign-ups", html)
        self.assertIn(self.student.display_name, html)
        self.assertIn(signup.get_status_display(), html)
        self.assertIn(self.departure.display_label, html)
        self.assertNotIn("Approved cost: $350.00", html)
        self.assertNotIn("Permission form acknowledged", html)
        self.assertNotIn("Outstanding trip requirements", html)
        self.assertNotIn(">Withdraw</button>", html)
        self.assertNotIn("Add a student", html)
        self.assertNotIn("Add a mentor", html)
        self.assertRegex(
            html,
            r'(?s)<section class="card shadow-sm mt-3" '
            r'aria-labelledby="mentor-signups-title">.*?'
            r"Mentors are welcome to come along as chaperones:",
        )

        create_url = reverse("travel:event_create", args=[self.program.id])
        edit_url = reverse("travel:event_edit", args=[self.program.id, self.event.id])
        for url in (create_url, edit_url):
            self.assertEqual(self.client.get(url).status_code, 302)
            self.assertEqual(self.client.post(url, {}).status_code, 302)

        self.event.refresh_from_db()
        self.assertEqual(self.event.name, "Competition Trip")

    def test_student_roster_groups_statuses_then_sorts_students_by_name(self):
        signup_data = [
            ("Zoe", "Zulu", "Zara", TravelSignup.APPROVED),
            ("Amy", "Alpha", "", TravelSignup.APPROVED),
            ("Ivy", "Beta", "Bea", TravelSignup.INTERESTED),
            ("Ben", "Alpha", "", TravelSignup.INTERESTED),
            ("Eve", "Zulu", "", TravelSignup.DECLINED),
            ("Drew", "Alpha", "", TravelSignup.DECLINED),
            ("Will", "Withdrawn", "", TravelSignup.WITHDRAWN),
        ]
        for legal_first_name, last_name, preferred_first_name, status in signup_data:
            student = Student.objects.create(
                organization=self.organization,
                legal_first_name=legal_first_name,
                preferred_first_name=preferred_first_name,
                last_name=last_name,
                school=self.school,
                graduation_year=2027,
            )
            TravelSignup.objects.create(
                student=student,
                event=self.event,
                status=status,
            )

        expected_statuses = (
            TravelSignup.APPROVED,
            TravelSignup.INTERESTED,
            TravelSignup.DECLINED,
            TravelSignup.WITHDRAWN,
        )
        expected_names = (
            "Amy Alpha",
            "Zara Zulu",
            "Bea Beta",
            "Ben Alpha",
            "Drew Alpha",
            "Eve Zulu",
            "Will Withdrawn",
        )
        for username in ("lead", "mentor"):
            self.client.login(username=username, password="password")  # nosec B106
            response = self.client.get(
                reverse("travel:event_list", args=[self.program.id])
            )
            self.assertEqual(response.status_code, 200, msg=username)
            html = response.content.decode()
            roster_section_start = html.index('aria-labelledby="student-signups-title"')
            roster_section_end = html.index("</section>", roster_section_start)
            roster_section = html[roster_section_start:roster_section_end]
            roster_start = roster_section.index(
                '<div class="student-signup-status-group'
            )
            roster_html = roster_section[roster_start:]

            group_order = [
                roster_html.index(f'data-status="{status}"')
                for status in expected_statuses
            ]
            self.assertEqual(group_order, sorted(group_order), msg=username)
            for heading in ("Parent Approved", "Interested", "Declined", "Withdrawn"):
                self.assertIn(heading, roster_html)

            name_order = [roster_html.index(name) for name in expected_names]
            self.assertEqual(name_order, sorted(name_order), msg=username)

    def test_mentor_without_trip_read_permission_cannot_view_trips(self):
        from programs.models import RolePermission

        RolePermission.objects.update_or_create(
            organization=self.organization,
            role="Mentor",
            section="travel_details",
            defaults={"can_read": False, "can_write": False},
        )
        self.client.login(username="mentor", password="password")  # nosec B106

        url = reverse("travel:event_list", args=[self.program.id])
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_students_and_parents_do_not_see_the_full_student_roster(self):
        TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            status=TravelSignup.APPROVED,
        )
        other_student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Other",
            last_name="Student",
            school=self.school,
            graduation_year=2027,
        )
        TravelSignup.objects.create(
            student=other_student,
            event=self.event,
            status=TravelSignup.APPROVED,
        )

        url = reverse("travel:event_list", args=[self.program.id])
        for username in ("student", "parent"):
            self.client.login(username=username, password="password")  # nosec B106
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, msg=username)
            html = response.content.decode()
            self.assertNotIn("Student Sign-ups", html, msg=username)
            self.assertNotIn(other_student.display_name, html, msg=username)
            self.assertNotIn("Outstanding trip requirements", html, msg=username)

    def test_lead_roster_shows_outstanding_trip_requirements(self):
        self.event.permission_form = "travel/forms/blank.pdf"
        self.event.requires_departure = True
        self.event.save()
        signup = TravelSignup.objects.create(student=self.student, event=self.event)
        self.client.force_login(self.lead)
        url = reverse("travel:event_list", args=[self.program.id])

        response = self.client.get(url)
        self.assertContains(response, "Outstanding trip requirements")
        self.assertContains(response, "Guardian approval")
        self.assertContains(response, "Signed permission form")
        self.assertContains(response, "Departure: Needed")
        self.assertContains(response, "Travel readiness")
        self.assertContains(response, "0 ready of 1 active sign-up")
        self.assertContains(response, f'href="#travel-signup-{signup.pk}"')

        signup.status = TravelSignup.APPROVED
        signup.departure = self.departure
        signup.permission_acknowledged = True
        signup.save()
        response = self.client.get(url)
        self.assertContains(response, "Signed permission form")
        self.assertNotContains(response, "Departure: Needed")
        self.assertNotContains(response, "Guardian approval: Pending")
        self.assertContains(response, "0 ready of 1 active sign-up")

        self.event.permission_form = ""
        self.event.requires_departure = False
        self.event.save()
        signup.departure = None
        signup.permission_acknowledged = False
        signup.save()
        response = self.client.get(url)
        self.assertContains(response, "No outstanding tracked trip requirements")
        self.assertNotContains(response, "Departure: Needed")
        self.assertNotContains(response, "Signed permission form: Needed")
        self.assertContains(response, "1 ready of 1 active sign-up")

        for status in (TravelSignup.DECLINED, TravelSignup.WITHDRAWN):
            signup.status = status
            signup.save()
            with self.subTest(status=status):
                response = self.client.get(url)
                self.assertNotContains(response, "Outstanding trip requirements")
                self.assertNotContains(
                    response, "No outstanding tracked trip requirements"
                )
                self.assertNotContains(response, "1 ready of 1 active sign-up")

    def test_family_readiness_is_visible_for_past_active_signups(self):
        past_date = timezone.localdate() - timedelta(days=2)
        past_event = TravelEvent.objects.create(
            program=self.program,
            name="Past Competition",
            start_date=past_date,
            end_date=past_date,
            requires_departure=True,
        )
        TravelSignup.objects.create(
            student=self.student,
            event=past_event,
            status=TravelSignup.INTERESTED,
        )
        url = reverse("travel:event_list", args=[self.program.pk])

        for username in ("student", "parent"):
            with self.subTest(username=username):
                self.client.login(username=username, password="password")  # nosec B106
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Past Competition")
                self.assertContains(response, "Guardian approval: Pending")
                self.assertContains(response, "Departure: Needed")

    def test_dashboard_shows_independent_readiness_only_for_local_signup(self):
        self.event.permission_form = "travel/forms/blank.pdf"
        self.event.requires_departure = True
        self.event.save()
        TravelSignup.objects.create(student=self.student, event=self.event)

        foreign_organization = Organization.objects.create(
            name="Foreign Dashboard Travel", slug="foreign-dashboard-travel"
        )
        foreign_event = TravelEvent.objects.create(
            organization=foreign_organization,
            program=self.program,
            name="Foreign Competition",
            start_date=self.event.start_date,
            end_date=self.event.end_date,
            permission_form="travel/forms/foreign-blank.pdf",
            requires_departure=True,
        )
        TravelSignup.objects.create(
            organization=foreign_organization,
            student=self.student,
            event=foreign_event,
        )

        for username in ("student", "parent"):
            with self.subTest(username=username):
                self.client.login(username=username, password="password")  # nosec B106
                response = self.client.get(reverse("profile_dashboard"))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Guardian approval: Pending")
                self.assertContains(response, "Signed permission form: Needed")
                self.assertContains(response, "Departure: Needed")
                self.assertNotContains(response, "Foreign Competition")

    def test_student_highlights_prefetch_readiness_for_multiple_signups(self):
        from travel.utils import get_student_travel_highlights

        self.student.organization = self.organization
        self.event.permission_form = "travel/forms/blank.pdf"
        self.event.save(update_fields=["permission_form"])
        TravelSignup.objects.create(student=self.student, event=self.event)
        second_event = TravelEvent.objects.create(
            program=self.program,
            name="Second Competition",
            start_date=self.event.start_date + timedelta(days=10),
            end_date=self.event.end_date + timedelta(days=10),
            permission_form="travel/forms/blank.pdf",
        )
        TravelSignup.objects.create(student=self.student, event=second_event)

        with self.assertNumQueries(4):
            signups = get_student_travel_highlights(self.student, self.program)
            readiness = [signup.readiness_requirements for signup in signups]

        self.assertEqual(len(signups), 2)
        self.assertTrue(
            all("Guardian approval" in [r["label"] for r in row] for row in readiness)
        )

    def test_mentor_with_trip_write_can_edit_but_not_manage_rosters_or_delete(self):
        from programs.models import RolePermission

        RolePermission.objects.update_or_create(
            organization=self.organization,
            role="Mentor",
            section="travel_details",
            defaults={"can_read": True, "can_write": True},
        )
        signup = TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            departure=self.departure,
            status=TravelSignup.INTERESTED,
        )
        self.client.login(username="mentor", password="password")  # nosec B106

        list_url = reverse("travel:event_list", args=[self.program.id])
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_add"])
        self.assertTrue(response.context["can_edit"])
        self.assertFalse(response.context["can_manage"])
        html = response.content.decode()
        self.assertIn(">Edit</a>", html)
        self.assertNotIn(">Delete</a>", html)
        self.assertIn("Student Sign-ups", html)
        self.assertIn(self.student.display_name, html)
        self.assertIn(signup.get_status_display(), html)
        self.assertNotIn("Add a student", html)
        self.assertNotIn(">Withdraw</button>", html)
        self.assertNotIn("Add a mentor", html)

        create_url = reverse("travel:event_create", args=[self.program.id])
        edit_url = reverse("travel:event_edit", args=[self.program.id, self.event.id])
        self.assertEqual(self.client.get(create_url).status_code, 200)
        self.assertEqual(self.client.get(edit_url).status_code, 200)

        payload = self.event_edit_payload()
        payload["name"] = "Mentor Updated Trip"
        response = self.client.post(edit_url, payload)
        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.name, "Mentor Updated Trip")
        delete_url = reverse(
            "travel:event_delete", args=[self.program.id, self.event.id]
        )
        self.assertEqual(self.client.get(delete_url).status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.name, "Mentor Updated Trip")

    def test_mentor_trip_roster_omits_inconsistent_organization_links(self):
        other_organization = Organization.objects.create(
            name="Travel Beta",
            slug="travel-beta",
            subdomain="travel-beta",
        )
        foreign_event = TravelEvent.objects.create(
            organization=other_organization,
            program=self.program,
            name="Foreign Trip",
            start_date=self.event.start_date,
            end_date=self.event.end_date,
        )
        foreign_departure = TravelDeparture.objects.create(
            organization=other_organization,
            event=foreign_event,
            label="Foreign Bus",
            leave_date=self.departure.leave_date,
        )
        foreign_student = Student.objects.create(
            organization=other_organization,
            legal_first_name="Foreign",
            last_name="Student",
            school=self.school,
            graduation_year=2027,
        )
        Student.objects.create(
            organization=self.organization,
            legal_first_name="Wrong Departure",
            last_name="Student",
            school=self.school,
            graduation_year=2027,
        )
        wrong_departure_student = Student.objects.get(
            legal_first_name="Wrong Departure"
        )
        misowned_student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Wrong Signup Owner",
            last_name="Student",
            school=self.school,
            graduation_year=2027,
        )
        TravelSignup.objects.create(
            organization=self.organization,
            student=foreign_student,
            event=self.event,
            departure=self.departure,
        )
        TravelSignup.objects.create(
            organization=self.organization,
            student=wrong_departure_student,
            event=self.event,
            departure=foreign_departure,
        )
        TravelSignup.objects.create(
            organization=other_organization,
            student=misowned_student,
            event=self.event,
            departure=self.departure,
        )

        self.client.login(username="mentor", password="password")  # nosec B106
        response = self.client.get(reverse("travel:event_list", args=[self.program.pk]))

        self.assertEqual(response.status_code, 200)
        event = response.context["events"][0]
        self.assertEqual(list(event.signups.all()), [])

    def test_mentor_with_trip_write_cannot_manage_student_signups(self):
        from programs.models import RolePermission

        RolePermission.objects.update_or_create(
            organization=self.organization,
            role="Mentor",
            section="travel_details",
            defaults={"can_read": True, "can_write": True},
        )
        signup = TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            status=TravelSignup.INTERESTED,
        )
        self.client.login(username="mentor", password="password")  # nosec B106

        add_url = reverse("travel:signup", args=[self.program.id, self.event.id])
        self.client.post(
            add_url,
            {"student": self.student.pk, "departure": self.departure.pk},
        )
        self.assertEqual(
            TravelSignup.objects.filter(event=self.event).count(),
            1,
        )

        withdraw_url = reverse(
            "travel:signup_withdraw", args=[self.program.id, signup.pk]
        )
        self.client.post(withdraw_url)
        signup.refresh_from_db()
        self.assertEqual(signup.status, TravelSignup.INTERESTED)

    def test_membership_lead_sees_trip_management_controls(self):
        self.client.login(username="lead", password="password")  # nosec B106
        url = reverse("travel:event_list", args=[self.program.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["can_manage"])
        self.assertTrue(resp.context["can_add"])
        html = resp.content.decode()
        self.assertIn("Add Trip", html)
        self.assertIn(">Edit</a>", html)
        self.assertIn(">Delete</a>", html)
        self.assertIn("Add a mentor", html)

    def test_lead_trip_rosters_and_add_forms_are_grouped_in_cards(self):
        second_departure = TravelDeparture.objects.create(
            event=self.event,
            label="Bus 2",
            leave_date=self.departure.leave_date,
        )
        TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            departure=self.departure,
            status=TravelSignup.APPROVED,
            approved_total="350.00",
        )
        parent = Adult.objects.create(
            organization=self.organization,
            legal_first_name="Alex",
            last_name="Chaperone",
            is_parent=True,
        )
        AdultStudentRelationship.objects.create(
            organization=self.organization,
            adult=parent,
            student=self.student,
        )
        TravelParentChaperoneSignup.objects.create(
            organization=self.organization,
            adult=parent,
            event=self.event,
            departure=second_departure,
        )
        mentor = Adult.objects.create(
            organization=self.organization,
            legal_first_name="Casey",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
        )
        TravelMentorSignup.objects.create(
            organization=self.organization,
            adult=mentor,
            event=self.event,
            departure=self.departure,
        )
        self.client.login(username="lead", password="password")  # nosec B106

        response = self.client.get(reverse("travel:event_list", args=[self.program.pk]))

        self.assertEqual(response.status_code, 200)
        page = response.content.decode()

        def get_card_contents(card_id):
            match = re.search(
                rf'<section class="card shadow-sm mt-3" '
                rf'aria-labelledby="{card_id}">(.*?)</section>',
                page,
                re.DOTALL,
            )
            self.assertIsNotNone(match, f"Missing participant card {card_id}")
            return match.group(1)

        student_card = get_card_contents("student-signups-title")
        parent_card = get_card_contents("parent-chaperones-title")
        mentor_card = get_card_contents("mentor-signups-title")

        self.assertIn("Test Student", student_card)
        self.assertIn("Add a student", student_card)
        self.assertIn("Bus 2", student_card)
        self.assertIn(f'id="id_departure_{self.event.pk}"', student_card)
        self.assertIn("Alex Chaperone", parent_card)
        self.assertIn("Add a parent", parent_card)
        self.assertIn("Bus 2", parent_card)
        self.assertRegex(
            parent_card,
            r'(?s)<select name="departure"[^>]*>.*?<option[^>]*>Bus 2',
        )
        self.assertIn("Casey Mentor", mentor_card)
        self.assertIn("Add a mentor", mentor_card)
        self.assertIn("Bus 2", mentor_card)
        self.assertIn(f'name="mentor-{self.event.pk}-departure"', mentor_card)
        self.assertLess(
            page.index("student-signups-title"), page.index("parent-chaperones-title")
        )
        self.assertLess(
            page.index("parent-chaperones-title"), page.index("mentor-signups-title")
        )

    def test_lead_sees_empty_student_parent_and_mentor_cards(self):
        self.client.login(username="lead", password="password")  # nosec B106

        response = self.client.get(reverse("travel:event_list", args=[self.program.pk]))

        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        for card_id, action in (
            ("student-signups-title", "Add a student"),
            ("parent-chaperones-title", "Add a parent"),
            ("mentor-signups-title", "Add a mentor"),
        ):
            match = re.search(
                rf'<section class="card shadow-sm mt-3" '
                rf'aria-labelledby="{card_id}">(.*?)</section>',
                page,
                re.DOTALL,
            )
            self.assertIsNotNone(match, f"Missing participant card {card_id}")
            self.assertIn(action, match.group(1))

    def test_edit_form_groups_trip_fields_into_sections(self):
        self.client.login(username="lead", password="password")  # nosec B106

        response = self.client.get(
            reverse("travel:event_edit", args=[self.program.pk, self.event.pk])
        )

        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        for heading in (
            "Trip details",
            "Location",
            "Plans and details",
            "Permission form",
        ):
            self.assertIn(heading, page)

    def test_trip_form_places_location_and_dates_in_paired_columns(self):
        self.client.login(username="lead", password="password")  # nosec B106
        urls = (
            reverse("travel:event_create", args=[self.program.pk]),
            reverse("travel:event_edit", args=[self.program.pk, self.event.pk]),
        )

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                trip_details = response.content.decode().split("</section>", 1)[0]
                fields = re.findall(
                    r'<div class="col-md-6">\s*<label for="id_([^"]+)"',
                    trip_details,
                )
                self.assertEqual(
                    fields, ["name", "location_name", "start_date", "end_date"]
                )

    def test_edit_notifies_approved_student_and_guardian_of_cost_change(self):
        signup = TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            status=TravelSignup.APPROVED,
            approved_total="300.00",
            balance_amount="300.00",
            balance_effective_date=self.event.start_date,
        )
        signup.cost_items.set([self.hotel])
        payload = self.event_edit_payload()
        payload["costs-0-amount"] = "425.00"
        self.client.login(username="lead", password="password")  # nosec B106

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("travel:event_edit", args=[self.program.pk, self.event.pk]),
                payload,
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertIn("alert-info", page)
        self.assertIn("Trip updated.", page)
        self.assertIn("students with pending or approved sign-ups", page)
        self.assertIn("eligible parents/guardians", page)
        self.assertIn("signed-up mentors and parent chaperones", page)
        signup.refresh_from_db()
        self.assertEqual(signup.status, TravelSignup.INTERESTED)
        self.assertEqual(signup.approved_total, None)
        self.assertEqual(signup.balance_amount, Decimal("425.00"))
        audit_entry = AuditLog.objects.get(
            organization=self.organization,
            event="TRAVEL_CHARGE_UPDATED",
            resource_type="TravelSignup",
            resource_id=str(signup.pk),
        )
        self.assertEqual(audit_entry.before["balance_amount"], "300.00")
        self.assertEqual(audit_entry.after["balance_amount"], "425.00")
        self.assertEqual(len(mail.outbox), 2)
        recipients = {message.to[0] for message in mail.outbox}
        self.assertEqual(recipients, {"student@example.com", "parent@example.com"})
        for message in mail.outbox:
            self.assertIn("425.00", message.body)
            self.assertIn("300.00", message.body)
            self.assertIn("approval", message.body.lower())

    def test_cost_repricing_locks_only_signups_with_nullable_departures(self):
        direct_signup = TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            status=TravelSignup.APPROVED,
            approved_total="300.00",
            balance_amount="300.00",
        )
        direct_signup.cost_items.set([self.hotel])
        departed_student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Departed",
            last_name="Student",
            school=self.school,
            graduation_year=2027,
        )
        departure_signup = TravelSignup.objects.create(
            student=departed_student,
            event=self.event,
            departure=self.departure,
            status=TravelSignup.APPROVED,
            approved_total="300.00",
            balance_amount="300.00",
        )
        departure_signup.cost_items.set([self.hotel])
        payload = self.event_edit_payload()
        payload["costs-0-amount"] = "425.00"
        self.client.login(username="lead", password="password")  # nosec B106

        original_select_for_update = QuerySet.select_for_update
        lock_scopes = []

        def capture_select_for_update(queryset, *args, **kwargs):
            if queryset.model is TravelSignup:
                lock_scopes.append(kwargs.get("of"))
            return original_select_for_update(queryset, *args, **kwargs)

        with patch.object(QuerySet, "select_for_update", capture_select_for_update):
            with self.captureOnCommitCallbacks(execute=False):
                response = self.client.post(
                    reverse("travel:event_edit", args=[self.program.pk, self.event.pk]),
                    payload,
                )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(lock_scopes, [("self",)])
        for signup in (direct_signup, departure_signup):
            signup.refresh_from_db()
            self.assertEqual(signup.status, TravelSignup.INTERESTED)
            self.assertIsNone(signup.approved_total)
            self.assertEqual(signup.balance_amount, Decimal("425.00"))

    def test_trip_cost_update_emails_keep_each_student_total_private(self):
        first_signup = TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            status=TravelSignup.APPROVED,
            approved_total="300.00",
            balance_amount="300.00",
        )
        first_signup.cost_items.set([self.hotel])

        second_student_user = User.objects.create_user(
            username="second-student",
            email="second-student@example.com",
        )
        second_student = Student.objects.create(
            organization=self.organization,
            user=second_student_user,
            legal_first_name="Second",
            last_name="Student",
            school=self.school,
            graduation_year=2027,
        )
        Enrollment.objects.create(
            organization=self.organization,
            student=second_student,
            program=self.program,
            active=True,
        )
        second_parent = Adult.objects.create(
            organization=self.organization,
            is_parent=True,
            login_enabled=True,
            email_updates=True,
            personal_email="second-parent@example.com",
        )
        AdultStudentRelationship.objects.create(
            organization=self.organization,
            adult=second_parent,
            student=second_student,
        )
        second_signup = TravelSignup.objects.create(
            organization=self.organization,
            student=second_student,
            event=self.event,
            status=TravelSignup.APPROVED,
            approved_total="350.00",
            balance_amount="350.00",
        )
        second_signup.cost_items.set([self.hotel, self.van])

        payload = self.event_edit_payload()
        payload["costs-0-amount"] = "425.00"
        self.client.force_login(self.lead)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("travel:event_edit", args=[self.program.pk, self.event.pk]),
                payload,
            )

        self.assertEqual(response.status_code, 302)
        messages_by_recipient = {message.to[0]: message.body for message in mail.outbox}
        self.assertEqual(
            set(messages_by_recipient),
            {
                "student@example.com",
                "parent@example.com",
                "second-student@example.com",
                "second-parent@example.com",
            },
        )
        for recipient in ("student@example.com", "parent@example.com"):
            self.assertIn("300.00", messages_by_recipient[recipient])
            self.assertIn("425.00", messages_by_recipient[recipient])
            self.assertNotIn("350.00", messages_by_recipient[recipient])
            self.assertNotIn("475.00", messages_by_recipient[recipient])
        for recipient in ("second-student@example.com", "second-parent@example.com"):
            self.assertIn("350.00", messages_by_recipient[recipient])
            self.assertIn("475.00", messages_by_recipient[recipient])
            self.assertNotIn("300.00", messages_by_recipient[recipient])
            self.assertNotIn("425.00", messages_by_recipient[recipient])

    def test_trip_edit_keeps_approval_when_student_total_does_not_change(self):
        signup = TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            status=TravelSignup.APPROVED,
            approved_total="300.00",
            balance_amount="300.00",
        )
        signup.cost_items.set([self.hotel])
        payload = self.event_edit_payload()
        payload["description"] = "Updated itinerary"
        self.client.force_login(self.lead)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("travel:event_edit", args=[self.program.pk, self.event.pk]),
                payload,
            )

        self.assertEqual(response.status_code, 302)
        signup.refresh_from_db()
        self.assertEqual(signup.status, TravelSignup.APPROVED)
        self.assertEqual(signup.approved_total, Decimal("300.00"))
        self.assertEqual(signup.balance_amount, Decimal("300.00"))
        self.assertEqual(len(mail.outbox), 2)

    def test_cost_change_notifies_pending_signup_of_its_new_total(self):
        signup = TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            status=TravelSignup.INTERESTED,
        )
        signup.cost_items.add(self.hotel)
        payload = self.event_edit_payload()
        payload["costs-0-amount"] = "425.00"
        self.client.login(username="lead", password="password")  # nosec B106

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("travel:event_edit", args=[self.program.pk, self.event.pk]),
                payload,
            )

        self.assertEqual(response.status_code, 302)
        signup.refresh_from_db()
        self.assertEqual(signup.status, TravelSignup.INTERESTED)
        self.assertIsNone(signup.approved_total)
        self.assertEqual(
            {message.to[0] for message in mail.outbox},
            {"student@example.com", "parent@example.com"},
        )
        for message in mail.outbox:
            self.assertIn("300.00", message.body)
            self.assertIn("425.00", message.body)
            self.assertIn("approval remains outstanding", message.body)

    def test_making_an_optional_fee_required_reprices_approved_signups(self):
        signup = TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            status=TravelSignup.APPROVED,
            approved_total="300.00",
            balance_amount="300.00",
        )
        signup.cost_items.set([self.hotel])
        payload = self.event_edit_payload()
        payload["costs-1-is_required"] = "on"
        self.client.force_login(self.lead)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("travel:event_edit", args=[self.program.pk, self.event.pk]),
                payload,
            )

        self.assertEqual(response.status_code, 302)
        self.van.refresh_from_db()
        signup.refresh_from_db()
        self.assertTrue(self.van.is_required)
        self.assertEqual(signup.status, TravelSignup.INTERESTED)
        self.assertEqual(signup.balance_amount, Decimal("350.00"))
        self.assertEqual(len(mail.outbox), 2)
        for message in mail.outbox:
            self.assertIn("300.00", message.body)
            self.assertIn("350.00", message.body)

    def test_trip_edit_does_not_reprice_cross_organization_signups(self):
        other_organization = Organization.objects.create(
            name="Travel Other",
            slug="travel-other",
            subdomain="travel-other",
        )
        foreign_student = Student.objects.create(
            organization=other_organization,
            legal_first_name="Foreign",
            last_name="Student",
            school=self.school,
            graduation_year=2027,
        )
        signup = TravelSignup.objects.create(
            organization=self.organization,
            student=foreign_student,
            event=self.event,
            status=TravelSignup.APPROVED,
            approved_total="300.00",
            balance_amount="300.00",
        )
        signup.cost_items.set([self.hotel])
        payload = self.event_edit_payload()
        payload["costs-0-amount"] = "425.00"
        self.client.force_login(self.lead)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("travel:event_edit", args=[self.program.pk, self.event.pk]),
                payload,
            )

        self.assertEqual(response.status_code, 302)
        signup.refresh_from_db()
        self.assertEqual(signup.status, TravelSignup.APPROVED)
        self.assertEqual(signup.approved_total, Decimal("300.00"))
        self.assertEqual(signup.balance_amount, Decimal("300.00"))
        self.assertEqual(mail.outbox, [])

    def test_student_signup_cannot_omit_a_required_cost(self):
        self.hotel.is_required = True
        self.hotel.save(update_fields=["is_required"])
        self.client.login(username="student", password="password")  # nosec B106
        page = self.client.get(reverse("travel:event_list", args=[self.program.pk]))
        self.assertContains(page, "Required")

        response = self.client.post(
            reverse("travel:signup", args=[self.program.pk, self.event.pk]),
            {},
        )

        self.assertEqual(response.status_code, 302)
        signup = TravelSignup.objects.get(student=self.student, event=self.event)
        self.assertEqual(list(signup.cost_items.all()), [self.hotel])
        self.assertEqual(signup.total_cost, Decimal("300.00"))

    def test_edit_with_changes_shows_notice_when_no_approved_recipients_exist(self):
        payload = self.event_edit_payload()
        payload["description"] = "Updated itinerary"
        self.client.force_login(self.lead)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("travel:event_edit", args=[self.program.pk, self.event.pk]),
                payload,
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertIn("alert-info", page)
        self.assertIn("Trip updated.", page)
        self.assertIn("when email addresses are available", page)
        self.assertEqual(mail.outbox, [])

    def test_edit_notifies_signed_up_adults_without_approved_students(self):
        self.mentor_user.email = "mentor@example.com"
        self.mentor_user.save(update_fields=["email"])
        self.mentor_adult.personal_email = ""
        self.mentor_adult.save(update_fields=["personal_email"])
        for model, adult in (
            (TravelMentorSignup, self.mentor_adult),
            (TravelParentChaperoneSignup, self.parent_adult),
        ):
            model.objects.create(
                organization=self.organization,
                event=self.event,
                adult=adult,
                departure=self.departure,
            )
        payload = self.event_edit_payload()
        payload["costs-0-amount"] = "425.00"
        self.client.force_login(self.lead)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("travel:event_edit", args=[self.program.pk, self.event.pk]),
                payload,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            {message.to[0] for message in mail.outbox},
            {"mentor@example.com", "parent@example.com"},
        )
        self.assertEqual(len(mail.outbox), 2)
        for message in mail.outbox:
            self.assertIn("Hotel", message.body)
            self.assertNotIn("425.00", message.body)
            self.assertIn(
                "Families receive their own signup total separately", message.body
            )

    def test_edit_deduplicates_signed_up_guardian_and_mentor(self):
        TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            status=TravelSignup.APPROVED,
            approved_total="300.00",
        )
        self.parent_user.email = "other-parent-address@example.com"
        self.parent_user.save(update_fields=["email"])
        for model in (TravelMentorSignup, TravelParentChaperoneSignup):
            model.objects.create(
                organization=self.organization,
                event=self.event,
                adult=self.parent_adult,
                departure=self.departure,
            )
        shared_email_mentor = Adult.objects.create(
            organization=self.organization,
            legal_first_name="Shared",
            last_name="Address",
            personal_email="PARENT@example.com",
            login_enabled=True,
            email_updates=True,
        )
        TravelMentorSignup.objects.create(
            organization=self.organization, event=self.event, adult=shared_email_mentor
        )
        payload = self.event_edit_payload()
        payload["description"] = "Updated itinerary"
        self.client.force_login(self.lead)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("travel:event_edit", args=[self.program.pk, self.event.pk]),
                payload,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(
            {message.to[0] for message in mail.outbox},
            {"student@example.com", "parent@example.com"},
        )

    def test_edit_excludes_ineligible_or_foreign_adult_signups(self):
        foreign_org = Organization.objects.create(
            name="Foreign Trip Attendees",
            slug="foreign-trip-attendees",
            subdomain="foreign-trip-attendees",
        )
        foreign_departure = TravelDeparture.objects.create(
            organization=foreign_org,
            event=self.event,
            label="Foreign departure",
            leave_date=self.departure.leave_date,
        )
        other_event = TravelEvent.objects.create(
            organization=self.organization,
            program=self.program,
            name="Other trip",
            start_date=self.event.start_date,
            end_date=self.event.end_date,
        )
        other_departure = TravelDeparture.objects.create(
            organization=self.organization,
            event=other_event,
            label="Other departure",
            leave_date=self.departure.leave_date,
        )
        for model, label in (
            (TravelMentorSignup, "mentor"),
            (TravelParentChaperoneSignup, "parent"),
        ):
            for case, adult_org, signup_org, enabled, opted_in in (
                ("valid", self.organization, self.organization, True, True),
                ("opted-out", self.organization, self.organization, True, False),
                ("disabled", self.organization, self.organization, False, True),
                ("foreign-adult", foreign_org, self.organization, True, True),
                ("foreign-signup", self.organization, foreign_org, True, True),
            ):
                adult = Adult.objects.create(
                    organization=adult_org,
                    legal_first_name=case,
                    last_name=label,
                    personal_email=f"{case}-{label}@example.com",
                    login_enabled=enabled,
                    email_updates=opted_in,
                )
                model.objects.create(
                    organization=signup_org, event=self.event, adult=adult
                )
            for case, departure in (
                ("foreign-departure", foreign_departure),
                ("other-departure", other_departure),
                ("no-address", None),
            ):
                adult = Adult.objects.create(
                    organization=self.organization,
                    legal_first_name=case,
                    last_name=label,
                    personal_email=(
                        "" if case == "no-address" else f"{case}-{label}@example.com"
                    ),
                    login_enabled=True,
                    email_updates=True,
                )
                model.objects.create(
                    organization=self.organization,
                    event=self.event,
                    adult=adult,
                    departure=departure,
                )
        payload = self.event_edit_payload()
        payload["description"] = "Updated itinerary"
        self.client.force_login(self.lead)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("travel:event_edit", args=[self.program.pk, self.event.pk]),
                payload,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(
            {message.to[0] for message in mail.outbox},
            {"valid-mentor@example.com", "valid-parent@example.com"},
        )

    def test_edit_notifies_approved_people_of_departure_and_permission_form_changes(
        self,
    ):
        TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            status=TravelSignup.APPROVED,
            approved_total="300.00",
        )
        payload = self.event_edit_payload()
        departure_date = self.departure.leave_date + timedelta(days=1)
        payload.update(
            {
                "departures-TOTAL_FORMS": "2",
                "departures-1-label": "Airport Group",
                "departures-1-leave_date": departure_date.isoformat(),
                "departures-1-leave_time": "08:30",
                "departures-1-meeting_location": "Main terminal",
                "departures-1-notes": "Meet at the main entrance",
            }
        )
        payload["departures-0-leave_date"] = departure_date.isoformat()
        payload["departures-0-leave_time"] = "07:30"
        payload["permission_form"] = SimpleUploadedFile(
            "new-permission.pdf", b"%PDF-1.4 travel permission", "application/pdf"
        )
        self.client.login(username="lead", password="password")  # nosec B106

        with TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.post(
                        reverse(
                            "travel:event_edit", args=[self.program.pk, self.event.pk]
                        ),
                        payload,
                    )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 2)
        for message in mail.outbox:
            self.assertIn("Permission form", message.body)
            self.assertIn("Departure", message.body)
            self.assertIn("Airport Group", message.body)
            self.assertNotIn("new-permission.pdf", message.body)

    def test_noop_or_invalid_edit_does_not_notify_approved_people(self):
        TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            status=TravelSignup.APPROVED,
            approved_total="300.00",
        )
        TravelMentorSignup.objects.create(
            organization=self.organization, event=self.event, adult=self.mentor_adult
        )
        TravelParentChaperoneSignup.objects.create(
            organization=self.organization, event=self.event, adult=self.parent_adult
        )
        self.client.login(username="lead", password="password")  # nosec B106
        edit_url = reverse("travel:event_edit", args=[self.program.pk, self.event.pk])

        with self.captureOnCommitCallbacks(execute=True):
            unchanged_response = self.client.post(
                edit_url, self.event_edit_payload(), follow=True
            )
        self.assertEqual(unchanged_response.status_code, 200)
        self.assertNotIn("Trip updated.", unchanged_response.content.decode())
        self.assertEqual(mail.outbox, [])

        invalid_payload = self.event_edit_payload()
        invalid_payload["end_date"] = (
            self.event.start_date - timedelta(days=1)
        ).isoformat()
        with self.captureOnCommitCallbacks(execute=True):
            invalid_response = self.client.post(edit_url, invalid_payload, follow=True)
        self.assertEqual(invalid_response.status_code, 200)
        self.assertNotIn("Trip updated.", invalid_response.content.decode())
        self.assertEqual(mail.outbox, [])

    def test_edit_does_not_notify_guardians_from_another_organization(self):
        foreign_organization = Organization.objects.create(
            name="Foreign Travel Notification Organization",
            slug="foreign-travel-notification",
            subdomain="foreign-travel-notification",
        )
        foreign_parent = Adult.objects.create(
            organization=foreign_organization,
            is_parent=True,
            login_enabled=True,
            email_updates=True,
            personal_email="foreign-parent@example.com",
        )
        AdultStudentRelationship.objects.create(
            organization=foreign_organization,
            adult=foreign_parent,
            student=self.student,
        )
        TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            status=TravelSignup.APPROVED,
            approved_total="300.00",
        )
        payload = self.event_edit_payload()
        payload["costs-0-amount"] = "425.00"
        self.client.login(username="lead", password="password")  # nosec B106

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("travel:event_edit", args=[self.program.pk, self.event.pk]),
                payload,
            )

        self.assertEqual(response.status_code, 302)
        recipients = {message.to[0] for message in mail.outbox}
        self.assertEqual(recipients, {"student@example.com", "parent@example.com"})

    def test_list_view_accessible_to_all_roles(self):
        for username in ("student", "parent", "mentor", "lead"):
            self.client.login(username=username, password="password")  # nosec B106
            url = reverse("travel:event_list", args=[self.program.id])
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, msg=username)

    def test_lead_role_is_scoped_to_the_current_organization(self):
        other = Organization.objects.create(
            name="Other Organization", slug="other", subdomain="other"
        )
        UserOrganizationMembership.objects.create(
            user=self.lead,
            organization=other,
            role="Mentor",
            is_active=True,
        )
        program = Program.objects.create(name="Other Program", organization=other)
        program.features.add(self.feature)
        event = TravelEvent.objects.create(
            organization=other,
            program=program,
            name="Other Trip",
            start_date=timezone.now().date() + timedelta(days=30),
            end_date=timezone.now().date() + timedelta(days=34),
        )
        student = Student.objects.create(
            organization=other,
            legal_first_name="Other",
            last_name="Student",
            school=School.objects.create(
                organization=other, name="Other Organization School"
            ),
            graduation_year=2027,
        )
        Enrollment.objects.create(
            organization=other, student=student, program=program, active=True
        )
        self.client.force_login(self.lead)

        list_url = reverse("travel:event_list", args=[program.id])
        response = self.client.get(list_url, HTTP_HOST="other.localhost")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_manage"])
        self.assertFalse(response.context["can_add"])
        self.assertNotContains(response, "Add Trip")
        self.assertNotContains(response, ">Edit</a>")

        signup_url = reverse("travel:signup", args=[program.id, event.id])
        self.client.post(
            signup_url,
            {"student": student.pk},
            HTTP_HOST="other.localhost",
        )
        self.assertFalse(
            TravelSignup.objects.filter(student=student, event=event).exists()
        )

    def test_foreign_organization_program_is_not_accessible(self):
        other = Organization.objects.create(name="Other", slug="other")
        program = Program.objects.create(name="Foreign Program", organization=other)
        program.features.add(self.feature)
        self.client.force_login(self.lead)

        response = self.client.get(
            reverse("travel:event_list", args=[program.id]), HTTP_HOST="testserver"
        )

        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)

    # ── Create / edit (lead only) ─────────────────────────────────────────

    def _create_payload(self):
        start = timezone.now().date() + timedelta(days=40)
        return {
            "name": "New Trip",
            "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=4)).isoformat(),
            "departures-TOTAL_FORMS": "1",
            "departures-INITIAL_FORMS": "0",
            "departures-MIN_NUM_FORMS": "0",
            "departures-MAX_NUM_FORMS": "1000",
            "departures-0-label": "Bus A",
            "departures-0-leave_date": start.isoformat(),
            "costs-TOTAL_FORMS": "2",
            "costs-INITIAL_FORMS": "0",
            "costs-MIN_NUM_FORMS": "0",
            "costs-MAX_NUM_FORMS": "1000",
            "costs-0-name": "Hotel",
            "costs-0-amount": "300.00",
            "costs-0-unit_label": "per person",
            "costs-0-sort_order": "0",
            "costs-1-name": "Van",
            "costs-1-amount": "50.00",
            "costs-1-unit_label": "per person",
            "costs-1-sort_order": "1",
        }

    def test_lead_can_create_trip_with_departure_and_costs(self):
        self.client.login(username="lead", password="password")  # nosec B106
        url = reverse("travel:event_create", args=[self.program.id])
        resp = self.client.post(url, self._create_payload())
        self.assertEqual(resp.status_code, 302)
        event = TravelEvent.objects.get(name="New Trip")
        self.assertEqual(event.program, self.program)
        self.assertEqual(event.departures.count(), 1)
        self.assertEqual(event.cost_options.count(), 2)

    def test_student_and_parent_cannot_create_trip(self):
        for username in ("student", "parent"):
            self.client.login(username=username, password="password")  # nosec B106
            url = reverse("travel:event_create", args=[self.program.id])
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302, msg=username)

    def test_mentor_cannot_create_trip(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse("travel:event_create", args=[self.program.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)

    # ── Student signup lifecycle ──────────────────────────────────────────

    def test_student_signs_up_and_parents_are_emailed(self):
        self.assertEqual(
            {
                "program": self.program.organization_id,
                "event": self.event.organization_id,
                "student": self.student.organization_id,
                "parent": self.parent_adult.organization_id,
                "departure": self.departure.organization_id,
            },
            {
                key: self.organization.pk
                for key in ("program", "event", "student", "parent", "departure")
            },
        )
        self.client.login(username="student", password="password")  # nosec B106
        url = reverse("travel:signup", args=[self.program.id, self.event.id])
        form = TravelSignupForm(
            {"departure": self.departure.id, "cost_items": [self.hotel.id]},
            instance=TravelSignup(event=self.event, organization=self.organization),
            departures=self.event.departures.all(),
            cost_options=self.event.cost_options.all(),
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(
                url,
                {"departure": self.departure.id, "cost_items": [self.hotel.id]},
            )
        self.assertEqual(resp.status_code, 302)
        signup = TravelSignup.objects.get(student=self.student, event=self.event)
        self.assertEqual(signup.status, TravelSignup.INTERESTED)
        self.assertEqual(signup.departure, self.departure)

        subjects = {m.subject for m in mail.outbox}
        self.assertIn(
            "Travel: Test Student wants to go to Competition Trip - please approve",
            subjects,
        )
        self.assertIn("Travel: You signed up for Competition Trip", subjects)

    def test_free_trip_can_be_created_and_students_can_sign_up(self):
        start = timezone.localdate() + timedelta(days=40)
        payload = {
            "name": "Free Competition",
            "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=2)).isoformat(),
            "departures-TOTAL_FORMS": "1",
            "departures-INITIAL_FORMS": "0",
            "departures-MIN_NUM_FORMS": "0",
            "departures-MAX_NUM_FORMS": "1000",
            "departures-0-label": "",
            "departures-0-leave_date": "",
            "departures-0-leave_time": "",
            "departures-0-meeting_location": "",
            "departures-0-notes": "",
            "costs-TOTAL_FORMS": "1",
            "costs-INITIAL_FORMS": "0",
            "costs-MIN_NUM_FORMS": "0",
            "costs-MAX_NUM_FORMS": "1000",
            "costs-0-name": "",
            "costs-0-amount": "",
            "costs-0-unit_label": "per person",
            "costs-0-notes": "",
            "costs-0-sort_order": "0",
        }
        self.client.force_login(self.lead)
        response = self.client.post(
            reverse("travel:event_create", args=[self.program.pk]), payload
        )

        self.assertEqual(response.status_code, 302)
        event = TravelEvent.objects.get(name="Free Competition")
        self.assertEqual(event.cost_options.count(), 0)

        self.client.force_login(self.student_user)
        self.client.post(reverse("travel:signup", args=[self.program.pk, event.pk]))
        signup = TravelSignup.objects.get(event=event, student=self.student)
        self.assertEqual(signup.total_cost, Decimal("0.00"))

    def test_partially_filled_cost_option_is_still_validated(self):
        payload = self.event_edit_payload()
        payload["costs-0-amount"] = ""
        self.client.force_login(self.lead)

        response = self.client.post(
            reverse("travel:event_edit", args=[self.program.pk, self.event.pk]),
            payload,
        )

        self.assertEqual(response.status_code, 200)
        self.hotel.refresh_from_db()
        self.assertEqual(self.hotel.amount, Decimal("300.00"))

    def test_required_departure_blocks_missing_selection_but_accepts_valid_choice(self):
        self.client.force_login(self.lead)
        payload = self.event_edit_payload()
        payload["requires_departure"] = "on"
        self.client.post(
            reverse("travel:event_edit", args=[self.program.pk, self.event.pk]),
            payload,
        )
        self.event.refresh_from_db()
        self.assertTrue(self.event.requires_departure)

        self.client.force_login(self.student_user)
        list_response = self.client.get(
            reverse("travel:event_list", args=[self.program.pk])
        )
        self.assertIn(
            f'id="id_student_departure_{self.event.pk}" class="form-select form-select-sm" required',
            list_response.content.decode(),
        )
        self.assertIn("Choose a departure", list_response.content.decode())
        signup_url = reverse("travel:signup", args=[self.program.pk, self.event.pk])
        self.client.post(signup_url, {"cost_items": [self.hotel.pk]})
        self.assertFalse(
            TravelSignup.objects.filter(event=self.event, student=self.student).exists()
        )

        self.client.post(
            signup_url,
            {"departure": self.departure.pk, "cost_items": [self.hotel.pk]},
        )
        signup = TravelSignup.objects.get(event=self.event, student=self.student)
        self.assertEqual(signup.departure, self.departure)

    def test_enabling_required_departure_preserves_existing_signup_for_staff_update(
        self,
    ):
        signup = TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            status=TravelSignup.INTERESTED,
        )
        self.client.force_login(self.lead)
        event_payload = self.event_edit_payload()
        event_payload["requires_departure"] = "on"
        self.client.post(
            reverse("travel:event_edit", args=[self.program.pk, self.event.pk]),
            event_payload,
        )

        signup.refresh_from_db()
        self.assertIsNone(signup.departure)
        self.assertEqual(signup.status, TravelSignup.INTERESTED)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("travel:signup_departure", args=[self.program.pk, signup.pk]),
                {"departure": self.departure.pk},
            )

        self.assertEqual(response.status_code, 302)
        signup.refresh_from_db()
        self.assertEqual(signup.departure, self.departure)
        self.assertEqual(signup.status, TravelSignup.INTERESTED)

    def test_invalid_departure_choice_is_not_replaced_with_local_default(self):
        foreign_organization = Organization.objects.create(
            name="Foreign Travel", slug="foreign-travel", subdomain="foreign-travel"
        )
        foreign_departure = TravelDeparture.objects.create(
            organization=foreign_organization,
            event=self.event,
            label="Foreign Bus",
            leave_date=self.event.start_date,
        )
        self.client.force_login(self.student_user)

        self.client.post(
            reverse("travel:signup", args=[self.program.pk, self.event.pk]),
            {"departure": foreign_departure.pk, "cost_items": [self.hotel.pk]},
        )

        self.assertFalse(
            TravelSignup.objects.filter(event=self.event, student=self.student).exists()
        )

    def test_foreign_cost_choice_is_rejected(self):
        foreign_organization = Organization.objects.create(
            name="Foreign Cost Org",
            slug="foreign-cost-org",
            subdomain="foreign-cost-org",
        )
        foreign_cost = TravelCostOption.objects.create(
            organization=foreign_organization,
            event=self.event,
            name="Foreign Flight",
            amount="500.00",
        )
        self.client.force_login(self.student_user)

        self.client.post(
            reverse("travel:signup", args=[self.program.pk, self.event.pk]),
            {"departure": self.departure.pk, "cost_items": [foreign_cost.pk]},
        )

        self.assertFalse(
            TravelSignup.objects.filter(event=self.event, student=self.student).exists()
        )

    def test_student_can_edit_choices_and_changed_choices_need_parent_approval(self):
        second_departure = TravelDeparture.objects.create(
            event=self.event,
            label="Bus 2",
            leave_date=self.event.start_date + timedelta(days=1),
        )
        signup = TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            departure=self.departure,
        )
        signup.cost_items.set([self.hotel, self.van])
        signup.approve(self.parent_adult)
        signup.save()
        self.client.force_login(self.student_user)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("travel:signup_edit", args=[self.program.pk, signup.pk]),
                {"departure": second_departure.pk, "cost_items": [self.hotel.pk]},
            )

        self.assertEqual(response.status_code, 302)
        signup.refresh_from_db()
        self.assertEqual(signup.departure, second_departure)
        self.assertEqual(signup.status, TravelSignup.INTERESTED)
        self.assertIsNone(signup.approved_total)
        self.assertIsNone(signup.approved_by)
        self.assertEqual(signup.balance_amount, Decimal("300.00"))
        self.assertEqual(set(signup.cost_items.all()), {self.hotel})
        self.assertEqual(
            AuditLog.objects.filter(
                organization=self.organization,
                event="TRAVEL_CHARGE_UPDATED",
                resource_id=str(signup.pk),
            ).count(),
            1,
        )
        self.assertEqual(
            {message.to[0] for message in mail.outbox},
            {"student@example.com", "parent@example.com"},
        )
        for message in mail.outbox:
            self.assertIn("approval", message.body.lower())
            self.assertIn("300.00", message.body)
            self.assertIn("350.00", message.body)

    def test_student_noop_edit_keeps_approval_and_sends_no_notice(self):
        signup = TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            departure=self.departure,
        )
        signup.cost_items.set([self.hotel, self.van])
        signup.approve(self.parent_adult)
        signup.save()
        self.client.force_login(self.student_user)

        response = self.client.post(
            reverse("travel:signup_edit", args=[self.program.pk, signup.pk]),
            {
                "departure": self.departure.pk,
                "cost_items": [self.hotel.pk, self.van.pk],
            },
        )

        self.assertEqual(response.status_code, 302)
        signup.refresh_from_db()
        self.assertEqual(signup.status, TravelSignup.APPROVED)
        self.assertEqual(signup.approved_total, Decimal("350.00"))
        self.assertEqual(mail.outbox, [])

    def test_student_cannot_edit_another_students_signup(self):
        other_user = User.objects.create_user(
            username="other-student", password="password"
        )  # nosec B106
        other_student = Student.objects.create(
            user=other_user,
            legal_first_name="Other",
            last_name="Student",
            school=self.school,
            graduation_year=2027,
        )
        Enrollment.objects.create(student=other_student, program=self.program)
        signup = TravelSignup.objects.create(student=self.student, event=self.event)
        self.client.force_login(other_user)

        self.client.post(
            reverse("travel:signup_edit", args=[self.program.pk, signup.pk]),
            {"departure": self.departure.pk},
        )

        signup.refresh_from_db()
        self.assertIsNone(signup.departure)
        self.assertEqual(signup.status, TravelSignup.INTERESTED)

    def test_lead_can_change_departure_without_reapproval_and_notifies_family(self):
        second_departure = TravelDeparture.objects.create(
            event=self.event,
            label="Airport Group",
            leave_date=self.event.start_date + timedelta(days=1),
        )
        signup = TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            departure=self.departure,
            status=TravelSignup.APPROVED,
            approved_total=Decimal("300.00"),
            balance_amount=Decimal("300.00"),
            approved_by=self.parent_adult,
        )
        departure_form = TravelSignupDepartureForm(
            {"departure": second_departure.pk},
            instance=signup,
            departures=self.event.departures.all(),
        )
        self.assertTrue(departure_form.is_valid(), departure_form.errors.as_json())
        self.client.force_login(self.lead)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("travel:signup_departure", args=[self.program.pk, signup.pk]),
                {"departure": second_departure.pk},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url, reverse("travel:event_list", args=[self.program.pk])
        )
        signup.refresh_from_db()
        self.assertEqual(
            signup.departure,
            second_departure,
            [str(message) for message in response.wsgi_request._messages],
        )
        self.assertEqual(signup.status, TravelSignup.INTERESTED)
        self.assertIsNone(signup.approved_total)
        self.assertIsNone(signup.approved_by)
        self.assertEqual(signup.balance_amount, Decimal("300.00"))
        self.assertEqual(
            {message.to[0] for message in mail.outbox},
            {"student@example.com", "parent@example.com"},
        )
        for message in mail.outbox:
            self.assertIn(self.departure.display_label, message.body)
            self.assertIn(second_departure.display_label, message.body)
            self.assertIn("approval", message.body.lower())

        mail.outbox = []
        response = self.client.post(
            reverse("travel:signup_departure", args=[self.program.pk, signup.pk]),
            {"departure": second_departure.pk},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(mail.outbox, [])

    def test_non_lead_cannot_change_a_students_departure(self):
        second_departure = TravelDeparture.objects.create(
            event=self.event,
            label="Airport Group",
            leave_date=self.event.start_date + timedelta(days=1),
        )
        signup = TravelSignup.objects.create(
            student=self.student, event=self.event, departure=self.departure
        )
        self.client.force_login(self.mentor_user)

        self.client.post(
            reverse("travel:signup_departure", args=[self.program.pk, signup.pk]),
            {"departure": second_departure.pk},
        )

        signup.refresh_from_db()
        self.assertEqual(signup.departure, self.departure)
        self.assertEqual(mail.outbox, [])

    def test_student_with_foreign_profile_cannot_sign_up(self):
        other_org = Organization.objects.create(
            name="Other Organization", slug="other-travel", subdomain="other-travel"
        )
        user = User.objects.create_user(
            username="foreign-profile-student", password="password"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=user,
            organization=self.organization,
            role="Student",
            is_active=True,
        )
        student = Student.objects.create(
            organization=other_org,
            user=user,
            legal_first_name="Foreign",
            last_name="Student",
            school=self.school,
            graduation_year=2027,
        )
        Enrollment.objects.create(
            organization=self.organization,
            student=student,
            program=self.program,
            active=True,
        )

        self.client.force_login(user)
        self.client.post(
            reverse("travel:signup", args=[self.program.id, self.event.id]),
            {"departure": self.departure.id},
        )

        self.assertFalse(
            TravelSignup.objects.filter(student=student, event=self.event).exists()
        )

    def test_student_without_active_enrollment_cannot_sign_up(self):
        user = User.objects.create_user(
            username="unenrolled-student", password="password"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=user,
            organization=self.organization,
            role="Student",
            is_active=True,
        )
        student = Student.objects.create(
            organization=self.organization,
            user=user,
            legal_first_name="Unenrolled",
            last_name="Student",
            school=self.school,
            graduation_year=2027,
        )

        self.client.force_login(user)
        self.client.post(
            reverse("travel:signup", args=[self.program.id, self.event.id]),
            {"departure": self.departure.id},
        )

        self.assertFalse(
            TravelSignup.objects.filter(student=student, event=self.event).exists()
        )

    def test_lead_can_add_student_still_needs_approval(self):
        self.client.login(username="lead", password="password")  # nosec B106
        url = reverse("travel:signup", args=[self.program.id, self.event.id])
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(
                url,
                {
                    "student": self.student.id,
                    "departure": self.departure.id,
                    "cost_items": [self.hotel.id],
                },
            )
        self.assertEqual(resp.status_code, 302)
        signup = TravelSignup.objects.get(student=self.student, event=self.event)
        self.assertEqual(signup.status, TravelSignup.INTERESTED)
        self.assertIn(
            "Travel: Test Student wants to go to Competition Trip - please approve",
            {m.subject for m in mail.outbox},
        )

    def test_second_student_cannot_sign_up_for_another(self):
        other_user = User.objects.create_user(
            username="other", password="password"
        )  # nosec B106
        Student.objects.create(
            user=other_user,
            legal_first_name="Other",
            last_name="Kid",
            school=self.school,
            graduation_year=2027,
        )
        self.client.login(username="other", password="password")  # nosec B106
        url = reverse("travel:signup", args=[self.program.id, self.event.id])
        resp = self.client.post(url, {"departure": self.departure.id})
        self.assertEqual(resp.status_code, 302)

        # Targeting a different student's signup id must not withdraw anyone.
        target = TravelSignup.objects.create(student=self.student, event=self.event)
        withdraw_url = reverse(
            "travel:signup_withdraw", args=[self.program.id, target.id]
        )
        resp = self.client.post(withdraw_url)
        target.refresh_from_db()
        self.assertEqual(target.status, TravelSignup.INTERESTED)

    def test_parent_approves_and_record_is_snapshotted(self):
        signup = TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            departure=self.departure,
        )
        signup.cost_items.set([self.hotel, self.van])
        self.client.login(username="parent", password="password")  # nosec B106
        url = reverse("travel:signup_approve", args=[self.program.id, signup.id])
        resp = self.client.post(url, {"approval_token": signup.approval_token})
        self.assertEqual(resp.status_code, 302)
        signup.refresh_from_db()
        self.assertEqual(signup.status, TravelSignup.APPROVED)
        self.assertEqual(signup.approved_total, 350)

    def test_parent_cannot_approve_other_parents_student(self):
        other_parent_user = User.objects.create_user(
            username="other_parent", password="password"
        )  # nosec B106
        Adult.objects.create(
            user=other_parent_user,
            is_parent=True,
            login_enabled=True,
            email_updates=True,
            personal_email="other@example.com",
        )
        signup = TravelSignup.objects.create(student=self.student, event=self.event)
        self.client.login(username="other_parent", password="password")  # nosec B106
        url = reverse("travel:signup_approve", args=[self.program.id, signup.id])
        resp = self.client.post(url, {"approval_token": signup.approval_token})
        self.assertEqual(resp.status_code, 302)
        signup.refresh_from_db()
        self.assertEqual(signup.status, TravelSignup.INTERESTED)

    def test_lead_cannot_approve_signup(self):
        signup = TravelSignup.objects.create(student=self.student, event=self.event)
        self.client.login(username="lead", password="password")  # nosec B106
        url = reverse("travel:signup_approve", args=[self.program.id, signup.id])
        self.client.post(url, {"approval_token": signup.approval_token})
        signup.refresh_from_db()
        self.assertEqual(signup.status, TravelSignup.INTERESTED)

    def test_approval_requires_permission_acknowledgement_when_form_attached(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.event.permission_form = SimpleUploadedFile(
            "permission.pdf", b"%PDF-1.4 fake", content_type="application/pdf"
        )
        self.event.save()
        signup = TravelSignup.objects.create(student=self.student, event=self.event)
        self.client.login(username="parent", password="password")  # nosec B106
        url = reverse("travel:signup_approve", args=[self.program.id, signup.id])

        # Without the checkbox -> stays interested, no approval.
        resp = self.client.post(url, {"approval_token": signup.approval_token})
        self.assertEqual(resp.status_code, 302)
        signup.refresh_from_db()
        self.assertEqual(signup.status, TravelSignup.INTERESTED)

        # With the checkbox -> approved and acked.
        resp = self.client.post(
            url,
            {"permission_acknowledged": "1", "approval_token": signup.approval_token},
        )
        signup.refresh_from_db()
        self.assertEqual(signup.status, TravelSignup.APPROVED)
        self.assertTrue(signup.permission_acknowledged)
        self.assertEqual(
            signup.permission_acknowledged_by, self.parent_adult.display_name
        )

    def test_parent_can_decline(self):
        signup = TravelSignup.objects.create(student=self.student, event=self.event)
        self.client.login(username="parent", password="password")  # nosec B106
        url = reverse("travel:signup_decline", args=[self.program.id, signup.id])
        self.client.post(url)
        signup.refresh_from_db()
        self.assertEqual(signup.status, TravelSignup.DECLINED)

    def test_parent_can_undo_decision(self):
        signup = TravelSignup.objects.create(student=self.student, event=self.event)
        signup.decline()
        signup.save()
        self.client.login(username="parent", password="password")  # nosec B106
        url = reverse("travel:signup_undo", args=[self.program.id, signup.id])
        self.client.post(url)
        signup.refresh_from_db()
        self.assertEqual(signup.status, TravelSignup.INTERESTED)

    def test_student_can_withdraw_and_rejoin(self):
        signup = TravelSignup.objects.create(student=self.student, event=self.event)
        self.client.login(username="student", password="password")  # nosec B106
        url = reverse("travel:signup_withdraw", args=[self.program.id, signup.id])
        self.client.post(url)
        signup.refresh_from_db()
        self.assertEqual(signup.status, TravelSignup.WITHDRAWN)
        self.assertIn(
            "Travel: Test Student is no longer going to Competition Trip",
            {m.subject for m in mail.outbox},
        )

        rejoin_url = reverse("travel:signup_rejoin", args=[self.program.id, signup.id])
        self.client.post(rejoin_url)
        signup.refresh_from_db()
        self.assertEqual(signup.status, TravelSignup.INTERESTED)

    def test_signup_is_preserved_when_notification_fails_after_commit(self):
        url = reverse("travel:signup", args=[self.program.id, self.event.id])

        with patch(
            "travel.views.notify_signup_created",
            side_effect=RuntimeError("mail transport failed"),
        ):
            with self.assertLogs("travel.views", level="ERROR") as log:
                with self.captureOnCommitCallbacks(execute=True) as callbacks:
                    self.client.login(
                        username="student", password="password"
                    )  # nosec B106
                    response = self.client.post(url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(callbacks), 1)
        self.assertTrue(
            TravelSignup.objects.filter(student=self.student, event=self.event).exists()
        )
        self.assertIn("error_type=RuntimeError", log.output[0])
        self.assertNotIn("mail transport failed", log.output[0])
        self.assertTrue(
            any(
                "could not be confirmed" in str(message)
                for message in response.wsgi_request._messages
            )
        )

    def test_rejoin_is_preserved_when_notification_fails_after_commit(self):
        signup = TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            status=TravelSignup.WITHDRAWN,
        )
        self.client.login(username="lead", password="password")  # nosec B106
        url = reverse("travel:signup_rejoin", args=[self.program.id, signup.id])

        with patch(
            "travel.views.notify_signup_created",
            side_effect=RuntimeError("mail transport failed"),
        ):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                response = self.client.post(url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(callbacks), 1)
        signup.refresh_from_db()
        self.assertEqual(signup.status, TravelSignup.INTERESTED)

    def test_signup_actions_reject_a_local_signup_linked_to_foreign_student(self):
        foreign_organization = Organization.objects.create(
            name="Travel Action Foreign",
            slug="travel-action-foreign",
            subdomain="travel-action-foreign",
        )
        foreign_student = Student.objects.create(
            organization=foreign_organization,
            legal_first_name="Foreign",
            last_name="Student",
        )
        withdrawn_signup = TravelSignup.objects.create(
            organization=self.organization,
            student=foreign_student,
            event=self.event,
            status=TravelSignup.WITHDRAWN,
        )
        self.assertEqual(
            (
                withdrawn_signup.organization_id,
                withdrawn_signup.event.organization_id,
                withdrawn_signup.event.program.organization_id,
                withdrawn_signup.student.organization_id,
            ),
            (
                self.organization.pk,
                self.organization.pk,
                self.organization.pk,
                foreign_organization.pk,
            ),
        )
        self.client.login(username="lead", password="password")  # nosec B106

        rejoin_response = self.client.post(
            reverse("travel:signup_rejoin", args=[self.program.id, withdrawn_signup.id])
        )
        withdrawn_signup.refresh_from_db()
        self.assertEqual(
            withdrawn_signup.status,
            TravelSignup.WITHDRAWN,
            rejoin_response.get("Location"),
        )
        withdrawn_signup.status = TravelSignup.INTERESTED
        withdrawn_signup.save(update_fields=["status"])
        withdraw_response = self.client.post(
            reverse(
                "travel:signup_withdraw", args=[self.program.id, withdrawn_signup.id]
            )
        )

        self.assertEqual(rejoin_response.status_code, 302)
        self.assertEqual(withdraw_response.status_code, 302)
        withdrawn_signup.refresh_from_db()
        self.assertEqual(withdrawn_signup.status, TravelSignup.INTERESTED)

        valid_signup = TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            status=TravelSignup.WITHDRAWN,
        )
        valid_rejoin_response = self.client.post(
            reverse("travel:signup_rejoin", args=[self.program.id, valid_signup.id])
        )
        self.assertEqual(valid_rejoin_response.status_code, 302)
        valid_signup.refresh_from_db()
        self.assertEqual(valid_signup.status, TravelSignup.INTERESTED)

    def test_signup_notifications_fail_closed_for_inconsistent_organization(self):
        from travel.services import (
            notify_mentor_signup_cancelled,
            notify_mentor_signup_created,
            notify_parent_chaperone_signup_cancelled,
            notify_parent_chaperone_signup_created,
            notify_signup_approved,
            notify_signup_created,
            notify_signup_decision_undone,
            notify_signup_declined,
            notify_signup_withdrawn,
        )

        foreign_organization = Organization.objects.create(
            name="Travel Notice Foreign",
            slug="travel-notice-foreign",
            subdomain="travel-notice-foreign",
        )
        signup = TravelSignup.objects.create(
            organization=foreign_organization,
            student=self.student,
            event=self.event,
        )

        for notifier in (
            notify_signup_created,
            notify_signup_approved,
            notify_signup_declined,
            notify_signup_decision_undone,
            notify_signup_withdrawn,
        ):
            with self.subTest(notifier=notifier.__name__):
                with patch("travel.services.send_templated_notification") as send:
                    notifier(signup)
                send.assert_not_called()

        mentor_signup = TravelMentorSignup.objects.create(
            organization=foreign_organization,
            adult=self.mentor_adult,
            event=self.event,
        )
        chaperone_signup = TravelParentChaperoneSignup.objects.create(
            organization=foreign_organization,
            adult=self.parent_adult,
            event=self.event,
        )
        for notifier, adult_signup in (
            (notify_mentor_signup_created, mentor_signup),
            (notify_mentor_signup_cancelled, mentor_signup),
            (notify_parent_chaperone_signup_created, chaperone_signup),
            (notify_parent_chaperone_signup_cancelled, chaperone_signup),
        ):
            with self.subTest(notifier=notifier.__name__):
                with patch("travel.services.send_templated_notification") as send:
                    notifier(adult_signup)
                send.assert_not_called()

    # ── Mentor signups & drivers ──────────────────────────────────────────

    def test_mentor_can_sign_up_and_cancel(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse("travel:mentor_signup", args=[self.program.id, self.event.id])
        resp = self.client.post(url, {"departure": self.departure.id})
        self.assertEqual(resp.status_code, 302)
        signup = TravelMentorSignup.objects.get(
            adult=self.mentor_adult, event=self.event
        )
        self.assertFalse(signup.is_driver)
        self.assertIn(
            "Travel: You're going to Competition Trip",
            {m.subject for m in mail.outbox},
        )

        cancel_url = reverse(
            "travel:mentor_cancel", args=[self.program.id, self.event.id]
        )
        resp = self.client.post(cancel_url)
        self.assertFalse(
            TravelMentorSignup.objects.filter(
                adult=self.mentor_adult, event=self.event
            ).exists()
        )

    def test_student_cannot_sign_up_as_mentor(self):
        self.client.login(username="student", password="password")  # nosec B106
        url = reverse("travel:mentor_signup", args=[self.program.id, self.event.id])
        self.client.post(url, {"departure": self.departure.id})
        self.assertFalse(TravelMentorSignup.objects.filter(event=self.event).exists())

    def test_driver_toggle_is_lead_only(self):
        mentor_signup = TravelMentorSignup.objects.create(
            adult=self.mentor_adult, event=self.event
        )
        url = reverse(
            "travel:mentor_driver_toggle", args=[self.program.id, mentor_signup.id]
        )
        for username in ("student", "parent", "mentor"):
            self.client.login(username=username, password="password")  # nosec B106
            self.client.post(url)
            mentor_signup.refresh_from_db()
            self.assertFalse(mentor_signup.is_driver, msg=username)

        self.client.login(username="lead", password="password")  # nosec B106
        self.client.post(url)
        mentor_signup.refresh_from_db()
        self.assertTrue(mentor_signup.is_driver)

    # ── Parent chaperones ─────────────────────────────────────────────────

    def test_eligible_parent_signs_up_to_chaperone_and_is_emailed(self):
        self.client.login(username="parent", password="password")  # nosec B106
        url = reverse("travel:chaperone_signup", args=[self.program.id, self.event.id])
        resp = self.client.post(url, {"departure": self.departure.id})
        self.assertEqual(resp.status_code, 302)
        signup = TravelParentChaperoneSignup.objects.get(
            adult=self.parent_adult, event=self.event
        )
        self.assertEqual(signup.departure, self.departure)
        self.assertIn(
            "Travel: You're signed up to chaperone Competition Trip",
            {m.subject for m in mail.outbox},
        )

    def test_parent_without_student_in_program_cannot_chaperone(self):
        other_parent_user = User.objects.create_user(
            username="other_parent", password="password"
        )  # nosec B106
        other_parent = Adult.objects.create(
            user=other_parent_user,
            is_parent=True,
            login_enabled=True,
            email_updates=True,
            personal_email="other@example.com",
        )
        self.client.login(username="other_parent", password="password")  # nosec B106
        url = reverse("travel:chaperone_signup", args=[self.program.id, self.event.id])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            TravelParentChaperoneSignup.objects.filter(
                adult=other_parent, event=self.event
            ).exists()
        )

    def test_lead_can_add_eligible_parent(self):
        self.client.login(username="lead", password="password")  # nosec B106
        url = reverse("travel:chaperone_signup", args=[self.program.id, self.event.id])
        resp = self.client.post(url, {"adult": self.parent_adult.id})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            TravelParentChaperoneSignup.objects.filter(
                adult=self.parent_adult, event=self.event
            ).exists()
        )

    def test_lead_cannot_add_ineligible_parent(self):
        other_parent_user = User.objects.create_user(
            username="other_parent", password="password"
        )  # nosec B106
        other_parent = Adult.objects.create(
            user=other_parent_user,
            is_parent=True,
            login_enabled=True,
            email_updates=True,
            personal_email="other@example.com",
        )
        self.client.login(username="lead", password="password")  # nosec B106
        url = reverse("travel:chaperone_signup", args=[self.program.id, self.event.id])
        resp = self.client.post(url, {"adult": other_parent.id})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            TravelParentChaperoneSignup.objects.filter(
                adult=other_parent, event=self.event
            ).exists()
        )

    def test_student_cannot_sign_up_as_chaperone(self):
        self.client.login(username="student", password="password")  # nosec B106
        url = reverse("travel:chaperone_signup", args=[self.program.id, self.event.id])
        self.client.post(url)
        self.assertFalse(
            TravelParentChaperoneSignup.objects.filter(event=self.event).exists()
        )

    def test_duplicate_signup_updates_departure(self):
        TravelParentChaperoneSignup.objects.create(
            adult=self.parent_adult, event=self.event
        )
        other_departure = TravelDeparture.objects.create(
            event=self.event,
            label="Bus 2",
            leave_date=timezone.now().date() + timedelta(days=31),
        )
        self.client.login(username="parent", password="password")  # nosec B106
        url = reverse("travel:chaperone_signup", args=[self.program.id, self.event.id])
        resp = self.client.post(url, {"departure": other_departure.id})
        self.assertEqual(resp.status_code, 302)
        signup = TravelParentChaperoneSignup.objects.get(
            adult=self.parent_adult, event=self.event
        )
        self.assertEqual(signup.departure, other_departure)
        self.assertEqual(
            TravelParentChaperoneSignup.objects.filter(
                adult=self.parent_adult, event=self.event
            ).count(),
            1,
        )

    def test_chaperone_signup_blocked_for_past_event(self):
        past_event = TravelEvent.objects.create(
            program=self.program,
            name="Old Trip",
            start_date=timezone.now().date() - timedelta(days=20),
            end_date=timezone.now().date() - timedelta(days=16),
        )
        self.client.login(username="parent", password="password")  # nosec B106
        url = reverse("travel:chaperone_signup", args=[self.program.id, past_event.id])
        self.client.post(url)
        self.assertFalse(
            TravelParentChaperoneSignup.objects.filter(
                adult=self.parent_adult, event=past_event
            ).exists()
        )

    def test_parent_can_cancel_own_chaperone_signup(self):
        signup = TravelParentChaperoneSignup.objects.create(
            adult=self.parent_adult, event=self.event
        )
        self.client.login(username="parent", password="password")  # nosec B106
        url = reverse("travel:chaperone_cancel", args=[self.program.id, self.event.id])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            TravelParentChaperoneSignup.objects.filter(pk=signup.pk).exists()
        )
        self.assertIn(
            "Travel: You're no longer chaperoning Competition Trip",
            {m.subject for m in mail.outbox},
        )

    def test_lead_can_remove_chaperone(self):
        signup = TravelParentChaperoneSignup.objects.create(
            adult=self.parent_adult, event=self.event
        )
        self.client.login(username="lead", password="password")  # nosec B106
        url = reverse("travel:chaperone_remove", args=[self.program.id, signup.pk])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            TravelParentChaperoneSignup.objects.filter(pk=signup.pk).exists()
        )

    def test_mentor_cannot_remove_chaperone(self):
        signup = TravelParentChaperoneSignup.objects.create(
            adult=self.parent_adult, event=self.event
        )
        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse("travel:chaperone_remove", args=[self.program.id, signup.pk])
        self.client.post(url)
        self.assertTrue(
            TravelParentChaperoneSignup.objects.filter(pk=signup.pk).exists()
        )

    def test_list_shows_chaperone_ui_by_role(self):
        self.client.login(username="parent", password="password")  # nosec B106
        url = reverse("travel:event_list", args=[self.program.id])
        resp = self.client.get(url)
        html = resp.content.decode()
        self.assertIn("I'd like to chaperone", html)

        # After signing up, the parent sees the cancel button instead.
        TravelParentChaperoneSignup.objects.create(
            adult=self.parent_adult, event=self.event
        )
        resp = self.client.get(url)
        html = resp.content.decode()
        self.assertNotIn("I'd like to chaperone", html)
        self.assertIn("I'm no longer available", html)

        # Mentors see neither, leads see an inline add form.
        self.client.login(username="mentor", password="password")  # nosec B106
        resp = self.client.get(url)
        html = resp.content.decode()
        self.assertNotIn("I'd like to chaperone", html)
        self.assertNotIn("I'm no longer available", html)

        self.client.login(username="lead", password="password")  # nosec B106
        resp = self.client.get(url)
        html = resp.content.decode()
        self.assertIn("Add a parent", html)
        self.assertIn("Add chaperone", html)

    # ── Delete ────────────────────────────────────────────────────────────

    def test_delete_is_lead_only(self):
        url = reverse("travel:event_delete", args=[self.program.id, self.event.id])
        for username in ("student", "mentor", "parent"):
            self.client.login(username=username, password="password")  # nosec B106
            self.client.post(url)
            self.assertTrue(
                TravelEvent.objects.filter(pk=self.event.id).exists(), msg=username
            )

        self.client.login(username="lead", password="password")  # nosec B106
        self.client.post(url)
        self.assertFalse(TravelEvent.objects.filter(pk=self.event.id).exists())

    # ── Sorting tests ─────────────────────────────────────────────────────

    def test_student_signups_sorted_by_preferred_name(self):
        s1 = Student.objects.create(
            legal_first_name="Zachary",
            preferred_first_name="Aaron",
            last_name="Smith",
            school=self.school,
            graduation_year=2027,
        )
        s2 = Student.objects.create(
            legal_first_name="Bob",
            preferred_first_name="",
            last_name="Jones",
            school=self.school,
            graduation_year=2027,
        )
        s3 = Student.objects.create(
            legal_first_name="Charles",
            preferred_first_name="Alice",
            last_name="Brown",
            school=self.school,
            graduation_year=2027,
        )
        for s in (s1, s2, s3):
            Enrollment.objects.create(student=s, program=self.program, active=True)

        # Create signups in reverse alphabetical order: Zachary (Aaron), Bob, Charles (Alice)
        TravelSignup.objects.create(student=s1, event=self.event)
        TravelSignup.objects.create(student=s2, event=self.event)
        TravelSignup.objects.create(student=s3, event=self.event)

        self.client.login(username="lead", password="password")  # nosec B106
        resp = self.client.get(reverse("travel:event_list", args=[self.program.id]))
        self.assertEqual(resp.status_code, 200)

        event = resp.context["events"][0]
        signup_names = [s.student.display_name for s in event.signups.all()]
        self.assertEqual(
            signup_names,
            ["Aaron Smith", "Alice Brown", "Bob Jones"],
        )

    def test_mentor_signups_sorted_by_preferred_name(self):
        m1 = Adult.objects.create(
            legal_first_name="Zachary",
            preferred_first_name="Aaron",
            last_name="Smith",
            is_mentor=True,
            mentor_active=True,
            personal_email="m1@example.com",
        )
        m2 = Adult.objects.create(
            legal_first_name="Bob",
            preferred_first_name="",
            last_name="Jones",
            is_mentor=True,
            mentor_active=True,
            personal_email="m2@example.com",
        )
        m3 = Adult.objects.create(
            legal_first_name="Charles",
            preferred_first_name="Alice",
            last_name="Brown",
            is_mentor=True,
            mentor_active=True,
            personal_email="m3@example.com",
        )

        # Create mentor signups in non-alphabetical order
        TravelMentorSignup.objects.create(adult=m1, event=self.event)
        TravelMentorSignup.objects.create(adult=m2, event=self.event)
        TravelMentorSignup.objects.create(adult=m3, event=self.event)

        self.client.login(username="lead", password="password")  # nosec B106
        resp = self.client.get(reverse("travel:event_list", args=[self.program.id]))
        self.assertEqual(resp.status_code, 200)

        event = resp.context["events"][0]
        mentor_names = [s.adult.display_name for s in event.mentor_signups.all()]
        self.assertEqual(
            mentor_names,
            ["Aaron Smith", "Alice Brown", "Bob Jones"],
        )

    def test_parent_chaperones_sorted_by_preferred_name(self):
        p1 = Adult.objects.create(
            legal_first_name="Zachary",
            preferred_first_name="Aaron",
            last_name="Smith",
            is_parent=True,
            personal_email="p1@example.com",
        )
        p2 = Adult.objects.create(
            legal_first_name="Bob",
            preferred_first_name="",
            last_name="Jones",
            is_parent=True,
            personal_email="p2@example.com",
        )
        p3 = Adult.objects.create(
            legal_first_name="Charles",
            preferred_first_name="Alice",
            last_name="Brown",
            is_parent=True,
            personal_email="p3@example.com",
        )

        # Create chaperone signups in non-alphabetical order
        TravelParentChaperoneSignup.objects.create(adult=p1, event=self.event)
        TravelParentChaperoneSignup.objects.create(adult=p2, event=self.event)
        TravelParentChaperoneSignup.objects.create(adult=p3, event=self.event)

        self.client.login(username="lead", password="password")  # nosec B106
        resp = self.client.get(reverse("travel:event_list", args=[self.program.id]))
        self.assertEqual(resp.status_code, 200)

        event = resp.context["events"][0]
        chaperone_names = [s.adult.display_name for s in event.parent_chaperones.all()]
        self.assertEqual(
            chaperone_names,
            ["Aaron Smith", "Alice Brown", "Bob Jones"],
        )

    def test_staff_signup_form_students_sorted_by_preferred_name(self):
        s1 = Student.objects.create(
            legal_first_name="Zachary",
            preferred_first_name="Aaron",
            last_name="Smith",
            school=self.school,
            graduation_year=2027,
        )
        s2 = Student.objects.create(
            legal_first_name="Bob",
            preferred_first_name="",
            last_name="Jones",
            school=self.school,
            graduation_year=2027,
        )
        s3 = Student.objects.create(
            legal_first_name="Charles",
            preferred_first_name="Alice",
            last_name="Brown",
            school=self.school,
            graduation_year=2027,
        )
        for s in (s1, s2, s3):
            Enrollment.objects.create(student=s, program=self.program, active=True)

        form = TravelStaffSignupForm(program=self.program)
        student_names = [s.display_name for s in form.fields["student"].queryset]
        self.assertEqual(
            student_names,
            ["Aaron Smith", "Alice Brown", "Bob Jones", "Test Student"],
        )

    def test_chaperone_staff_form_adults_sorted_by_preferred_name(self):
        self.parent_adult.legal_first_name = "David"
        self.parent_adult.last_name = "Parent"
        self.parent_adult.save()

        p1 = Adult.objects.create(
            legal_first_name="Zachary",
            preferred_first_name="Aaron",
            last_name="Smith",
            is_parent=True,
            personal_email="p1@example.com",
        )
        p2 = Adult.objects.create(
            legal_first_name="Bob",
            preferred_first_name="",
            last_name="Jones",
            is_parent=True,
            personal_email="p2@example.com",
        )
        p3 = Adult.objects.create(
            legal_first_name="Charles",
            preferred_first_name="Alice",
            last_name="Brown",
            is_parent=True,
            personal_email="p3@example.com",
        )
        for p in (p1, p2, p3):
            AdultStudentRelationship.objects.create(adult=p, student=self.student)

        form = TravelParentChaperoneStaffForm(program=self.program)
        adult_names = [a.display_name for a in form.fields["adult"].queryset]
        self.assertEqual(
            adult_names,
            ["Aaron Smith", "Alice Brown", "Bob Jones", "David Parent"],
        )

    def test_parent_view_children_sorted_by_preferred_name(self):
        s1 = Student.objects.create(
            legal_first_name="Zachary",
            preferred_first_name="Aaron",
            last_name="Smith",
            school=self.school,
            graduation_year=2027,
        )
        s2 = Student.objects.create(
            legal_first_name="Bob",
            preferred_first_name="",
            last_name="Jones",
            school=self.school,
            graduation_year=2027,
        )
        s3 = Student.objects.create(
            legal_first_name="Charles",
            preferred_first_name="Alice",
            last_name="Brown",
            school=self.school,
            graduation_year=2027,
        )
        for s in (s1, s2, s3):
            AdultStudentRelationship.objects.create(adult=self.parent_adult, student=s)

        self.client.login(username="parent", password="password")  # nosec B106
        resp = self.client.get(reverse("travel:event_list", args=[self.program.id]))
        self.assertEqual(resp.status_code, 200)

        parent_rows = resp.context["parent_rows"]
        child_names = [r["student"].display_name for r in parent_rows]
        self.assertEqual(
            child_names,
            ["Aaron Smith", "Alice Brown", "Bob Jones", "Test Student"],
        )

    def test_decision_notification_reaches_student_and_each_eligible_guardian(self):
        signup = TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            status=TravelSignup.INTERESTED,
        )
        self.client.force_login(self.parent_user)

        self.client.post(
            reverse("travel:signup_approve", args=[self.program.pk, signup.pk]),
            {"approval_token": signup.approval_token},
        )

        self.assertEqual(
            {message.to[0] for message in mail.outbox},
            {"student@example.com", "parent@example.com"},
        )

    def test_event_update_notifies_pending_signup_without_exposing_other_families(self):
        from travel.services import notify_approved_trip_changes

        TravelSignup.objects.create(
            student=self.student,
            event=self.event,
            status=TravelSignup.INTERESTED,
        )

        notify_approved_trip_changes(
            self.event.pk,
            self.organization.pk,
            [{"title": "Departure", "detail": "Changed schedule"}],
        )

        self.assertEqual(
            {message.to[0] for message in mail.outbox},
            {"student@example.com", "parent@example.com"},
        )
        self.assertTrue(
            all("Changed schedule" in message.body for message in mail.outbox)
        )

    def test_event_cancellation_notifies_active_families_and_signed_adults(self):
        signup = TravelSignup.objects.create(
            organization=self.organization,
            student=self.student,
            event=self.event,
            status=TravelSignup.APPROVED,
            approved_total=Decimal("300.00"),
            balance_amount=Decimal("300.00"),
        )
        TravelMentorSignup.objects.create(
            organization=self.organization,
            adult=self.mentor_adult,
            event=self.event,
        )
        TravelParentChaperoneSignup.objects.create(
            organization=self.organization,
            adult=self.parent_adult,
            event=self.event,
        )
        self.client.force_login(self.lead)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("travel:event_delete", args=[self.program.pk, self.event.pk])
            )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(TravelEvent.objects.filter(pk=self.event.pk).exists())
        self.assertEqual(
            {message.to[0] for message in mail.outbox},
            {
                "student@example.com",
                "parent@example.com",
                "mentor@example.com",
            },
        )
        for message in mail.outbox:
            self.assertIn("cancelled", message.subject.lower())
            self.assertIn(self.event.name, message.body)
            self.assertNotIn("/travel/", message.body)


class TravelOrganizationCreationTests(TestCase):
    organization_owned_models = (
        TravelEvent,
        TravelDeparture,
        TravelCostOption,
        TravelMentorSignup,
        TravelParentChaperoneSignup,
    )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._default_org_hook_models = []
        for model in cls.organization_owned_models:
            if post_init.disconnect(
                dispatch_uid=f"default-test-org-{model._meta.label}", sender=model
            ):
                cls._default_org_hook_models.append(model)

    @classmethod
    def tearDownClass(cls):
        try:
            if cls._default_org_hook_models:
                from GoSAdminPortal.test_runner import install_default_test_org_hook

                install_default_test_org_hook()
        finally:
            super().tearDownClass()

    def setUp(self):
        self.organization = Organization.objects.create(
            name="Travel Ownership Org",
            slug="travel-ownership",
            subdomain="travel-ownership",
        )
        self.host = "travel-ownership.localhost"
        self.feature, _ = ProgramFeature.objects.get_or_create(
            key="travel", defaults={"name": "Travel"}
        )
        self.program = Program.objects.create(
            organization=self.organization, name="Travel Ownership Program"
        )
        self.program.features.add(self.feature)
        self.lead = User.objects.create_user(
            username="travel-ownership-lead", password="password"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=self.lead,
            organization=self.organization,
            role="LeadMentor",
            is_active=True,
        )
        self.lead_adult = Adult.objects.create(
            organization=self.organization,
            user=self.lead,
            legal_first_name="Taylor",
            last_name="Mentor",
            is_mentor=True,
        )
        self.parent_user = User.objects.create_user(
            username="travel-ownership-parent", password="password"
        )  # nosec B106
        self.parent = Adult.objects.create(
            organization=self.organization,
            user=self.parent_user,
            legal_first_name="Parker",
            last_name="Parent",
            is_parent=True,
        )
        self.school = School.objects.create(name="Travel Ownership School")
        self.student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Sam",
            last_name="Student",
            school=self.school,
            graduation_year=2027,
        )
        Enrollment.objects.create(
            organization=self.organization,
            student=self.student,
            program=self.program,
            active=True,
        )
        AdultStudentRelationship.objects.create(
            organization=self.organization,
            adult=self.parent,
            student=self.student,
        )
        self.start_date = timezone.localdate() + timedelta(days=30)
        self.event = TravelEvent.objects.create(
            organization=self.organization,
            program=self.program,
            name="Existing Ownership Trip",
            start_date=self.start_date,
            end_date=self.start_date + timedelta(days=2),
        )
        self.departure = TravelDeparture.objects.create(
            organization=self.organization,
            event=self.event,
            label="Existing Bus",
            leave_date=self.start_date,
        )
        self.cost_option = TravelCostOption.objects.create(
            organization=self.organization,
            event=self.event,
            name="Existing Hotel",
            amount="100.00",
        )

    def tenant_post(self, url, data=None, user=None):
        self.client.force_login(user or self.lead)
        return self.client.post(url, data=data or {}, HTTP_HOST=self.host)

    def tenant_get(self, url, user=None):
        self.client.force_login(user or self.lead)
        return self.client.get(url, HTTP_HOST=self.host)

    def test_signup_mutation_views_reject_foreign_owned_signup(self):
        foreign_organization = Organization.objects.create(
            name="Travel Mutation Beta",
            slug="travel-mutation-beta",
            subdomain="travel-mutation-beta",
        )
        foreign_program = Program.objects.create(organization=foreign_organization)
        foreign_program.features.add(self.feature)
        foreign_user = User.objects.create_user(
            username="travel-mutation-foreign-student", password="password"
        )  # nosec B106
        foreign_student = Student.objects.create(
            organization=foreign_organization,
            user=foreign_user,
            legal_first_name="Foreign",
            last_name="Student",
        )
        Enrollment.objects.create(
            organization=foreign_organization,
            student=foreign_student,
            program=foreign_program,
            active=True,
        )
        foreign_event = TravelEvent.objects.create(
            organization=foreign_organization,
            program=foreign_program,
            name="Foreign Trip",
            start_date=self.start_date,
            end_date=self.start_date + timedelta(days=2),
        )
        foreign_departure = TravelDeparture.objects.create(
            organization=foreign_organization,
            event=foreign_event,
            label="Foreign Bus",
            leave_date=self.start_date,
        )
        foreign_signup = TravelSignup.objects.create(
            organization=foreign_organization,
            student=foreign_student,
            event=foreign_event,
            departure=foreign_departure,
            status=TravelSignup.APPROVED,
        )

        self.tenant_post(
            reverse(
                "travel:signup_departure", args=[self.program.pk, foreign_signup.pk]
            ),
            {"departure": self.departure.pk},
        )
        self.tenant_post(
            reverse("travel:signup_edit", args=[self.program.pk, foreign_signup.pk]),
            {"departure": self.departure.pk},
            user=foreign_user,
        )

        foreign_signup.refresh_from_db()
        self.assertEqual(foreign_signup.departure, foreign_departure)
        self.assertEqual(foreign_signup.status, TravelSignup.APPROVED)
        self.assertEqual(mail.outbox, [])

    def create_foreign_event_children(self):
        foreign_organization = Organization.objects.create(
            name="Travel Child Beta",
            slug="travel-child-beta",
            subdomain="travel-child-beta",
        )
        departure = TravelDeparture.objects.create(
            organization=foreign_organization,
            event=self.event,
            label="Beta Private Departure",
            leave_date=self.start_date + timedelta(days=1),
            meeting_location="Beta Private Location",
        )
        cost_option = TravelCostOption.objects.create(
            organization=foreign_organization,
            event=self.event,
            name="Beta Private Cost",
            amount="300.00",
            sort_order=99,
        )
        return departure, cost_option

    def test_event_delete_refuses_foreign_cascades_and_related_link_changes(self):
        foreign_organization = Organization.objects.create(
            name="Travel Delete Beta",
            slug="travel-delete-beta",
            subdomain="travel-delete-beta",
        )
        foreign_departure, foreign_cost = self.create_foreign_event_children()
        foreign_student = Student.objects.create(
            organization=foreign_organization,
            legal_first_name="Foreign",
            last_name="Signup Student",
        )
        foreign_student_with_departure = Student.objects.create(
            organization=foreign_organization,
            legal_first_name="Foreign",
            last_name="Departure Student",
        )
        foreign_student_with_cost = Student.objects.create(
            organization=foreign_organization,
            legal_first_name="Foreign",
            last_name="Cost Student",
        )
        foreign_program = Program.objects.create(
            organization=foreign_organization, name="Foreign Travel Program"
        )
        foreign_event = TravelEvent.objects.create(
            organization=foreign_organization,
            program=foreign_program,
            name="Foreign Trip",
            start_date=self.start_date,
            end_date=self.start_date + timedelta(days=2),
        )
        direct_signup = TravelSignup.objects.create(
            organization=foreign_organization,
            student=foreign_student,
            event=self.event,
        )
        departure_signup = TravelSignup.objects.create(
            organization=foreign_organization,
            student=foreign_student_with_departure,
            event=foreign_event,
            departure=self.departure,
        )
        cost_signup = TravelSignup.objects.create(
            organization=foreign_organization,
            student=foreign_student_with_cost,
            event=foreign_event,
        )
        cost_signup.cost_items.add(self.cost_option)
        foreign_mentor = Adult.objects.create(
            organization=foreign_organization,
            legal_first_name="Foreign",
            last_name="Mentor",
            is_mentor=True,
        )
        foreign_parent = Adult.objects.create(
            organization=foreign_organization,
            legal_first_name="Foreign",
            last_name="Parent",
            is_parent=True,
        )
        mentor_signup = TravelMentorSignup.objects.create(
            organization=foreign_organization,
            adult=foreign_mentor,
            event=self.event,
        )
        parent_signup = TravelParentChaperoneSignup.objects.create(
            organization=foreign_organization,
            adult=foreign_parent,
            event=self.event,
        )

        with patch("audit.mixins.log_deletion") as log_deletion:
            response = self.tenant_post(
                reverse("travel:event_delete", args=[self.program.pk, self.event.pk])
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(TravelEvent.objects.filter(pk=self.event.pk).exists())
        self.assertTrue(
            TravelDeparture.objects.filter(pk=foreign_departure.pk).exists()
        )
        self.assertTrue(TravelCostOption.objects.filter(pk=foreign_cost.pk).exists())
        self.assertTrue(TravelSignup.objects.filter(pk=direct_signup.pk).exists())
        departure_signup.refresh_from_db()
        self.assertEqual(departure_signup.departure_id, self.departure.pk)
        cost_signup.refresh_from_db()
        self.assertTrue(cost_signup.cost_items.filter(pk=self.cost_option.pk).exists())
        self.assertTrue(TravelMentorSignup.objects.filter(pk=mentor_signup.pk).exists())
        self.assertTrue(
            TravelParentChaperoneSignup.objects.filter(pk=parent_signup.pk).exists()
        )
        log_deletion.assert_not_called()

    def test_event_edit_hides_foreign_departures_and_costs(self):
        self.create_foreign_event_children()

        response = self.tenant_get(
            reverse("travel:event_edit", args=[self.program.pk, self.event.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Beta Private Departure")
        self.assertNotContains(response, "Beta Private Cost")

    def test_event_edit_cannot_modify_foreign_departures_and_costs(self):
        foreign_departure, foreign_cost = self.create_foreign_event_children()
        payload = {
            "name": self.event.name,
            "start_date": self.start_date.isoformat(),
            "end_date": (self.start_date + timedelta(days=2)).isoformat(),
            "location_name": self.event.location_name,
            "location_address": self.event.location_address,
            "description": self.event.description,
            "departures-TOTAL_FORMS": "2",
            "departures-INITIAL_FORMS": "2",
            "departures-MIN_NUM_FORMS": "0",
            "departures-MAX_NUM_FORMS": "1000",
            "departures-0-id": str(self.departure.pk),
            "departures-0-label": self.departure.label,
            "departures-0-leave_date": self.departure.leave_date.isoformat(),
            "departures-0-leave_time": "",
            "departures-0-meeting_location": self.departure.meeting_location,
            "departures-0-notes": self.departure.notes,
            "departures-1-id": str(foreign_departure.pk),
            "departures-1-label": "Tampered Foreign Departure",
            "departures-1-leave_date": foreign_departure.leave_date.isoformat(),
            "departures-1-leave_time": "",
            "departures-1-meeting_location": foreign_departure.meeting_location,
            "departures-1-notes": foreign_departure.notes,
            "costs-TOTAL_FORMS": "2",
            "costs-INITIAL_FORMS": "2",
            "costs-MIN_NUM_FORMS": "0",
            "costs-MAX_NUM_FORMS": "1000",
            "costs-0-id": str(self.cost_option.pk),
            "costs-0-name": self.cost_option.name,
            "costs-0-amount": str(self.cost_option.amount),
            "costs-0-unit_label": self.cost_option.unit_label,
            "costs-0-notes": self.cost_option.notes,
            "costs-0-sort_order": str(self.cost_option.sort_order),
            "costs-1-id": str(foreign_cost.pk),
            "costs-1-name": "Tampered Foreign Cost",
            "costs-1-amount": "999.00",
            "costs-1-unit_label": foreign_cost.unit_label,
            "costs-1-notes": foreign_cost.notes,
            "costs-1-sort_order": str(foreign_cost.sort_order),
        }

        self.tenant_post(
            reverse("travel:event_edit", args=[self.program.pk, self.event.pk]),
            payload,
        )

        foreign_departure.refresh_from_db()
        foreign_cost.refresh_from_db()
        self.assertEqual(foreign_departure.label, "Beta Private Departure")
        self.assertEqual(foreign_cost.name, "Beta Private Cost")
        self.assertEqual(foreign_cost.amount, 300)

    def test_mentor_signup_rejects_foreign_adult_profile(self):
        foreign_organization = Organization.objects.create(
            name="Travel Mentor Profile Beta",
            slug="travel-mentor-profile-beta",
            subdomain="travel-mentor-profile-beta",
        )
        foreign_mentor = User.objects.create_user(
            username="foreign-profile-lead", password="password"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=foreign_mentor,
            organization=self.organization,
            role="LeadMentor",
            is_active=True,
        )
        foreign_adult = Adult.objects.create(
            organization=foreign_organization,
            user=foreign_mentor,
            legal_first_name="Foreign",
            last_name="Mentor",
            is_mentor=True,
        )

        self.tenant_post(
            reverse("travel:mentor_signup", args=[self.program.pk, self.event.pk]),
            user=foreign_mentor,
        )

        self.assertFalse(
            TravelMentorSignup.objects.filter(
                event=self.event, adult=foreign_adult
            ).exists()
        )

    def create_foreign_parent_linked_to_local_student(self):
        foreign_organization = Organization.objects.create(
            name="Travel Parent Profile Beta",
            slug="travel-parent-profile-beta",
            subdomain="travel-parent-profile-beta",
        )
        foreign_parent = Adult.objects.create(
            organization=foreign_organization,
            legal_first_name="Foreign",
            last_name="Parent",
            is_parent=True,
        )
        AdultStudentRelationship.objects.create(
            organization=self.organization,
            adult=foreign_parent,
            student=self.student,
        )
        return foreign_parent

    def test_parent_eligibility_form_excludes_foreign_adult(self):
        foreign_parent = self.create_foreign_parent_linked_to_local_student()
        form = TravelParentChaperoneStaffForm(program=self.program)

        self.assertIn(self.parent, form.fields["adult"].queryset)
        self.assertNotIn(foreign_parent, form.fields["adult"].queryset)

    def test_mentor_staff_form_offers_only_active_local_mentors(self):
        active_mentor = Adult.objects.create(
            organization=self.organization,
            legal_first_name="Active",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
        )
        inactive_mentor = Adult.objects.create(
            organization=self.organization,
            legal_first_name="Inactive",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=False,
        )
        foreign_organization = Organization.objects.create(
            name="Foreign Mentor Organization",
            slug="foreign-mentor-organization",
            subdomain="foreign-mentor-organization",
        )
        foreign_mentor = Adult.objects.create(
            organization=foreign_organization,
            legal_first_name="Foreign",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
        )

        form = TravelMentorStaffSignupForm(
            program=self.program,
            departures=self.event.departures.all(),
        )

        self.assertIn(active_mentor, form.fields["adult"].queryset)
        self.assertNotIn(inactive_mentor, form.fields["adult"].queryset)
        self.assertNotIn(foreign_mentor, form.fields["adult"].queryset)

    def test_lead_can_add_active_local_mentor_to_trip(self):
        active_mentor = Adult.objects.create(
            organization=self.organization,
            legal_first_name="New",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
        )

        response = self.tenant_post(
            reverse("travel:mentor_add", args=[self.program.pk, self.event.pk]),
            {f"mentor-{self.event.pk}-adult": str(active_mentor.pk)},
        )

        self.assertEqual(response.status_code, 302)
        signup = TravelMentorSignup.objects.for_organization(self.organization).get(
            event=self.event, adult=active_mentor
        )
        self.assertEqual(signup.organization, self.organization)
        self.assertEqual(signup.departure, self.departure)

    def test_non_lead_cannot_add_mentor_to_trip(self):
        active_mentor = Adult.objects.create(
            organization=self.organization,
            legal_first_name="New",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
        )

        response = self.tenant_post(
            reverse("travel:mentor_add", args=[self.program.pk, self.event.pk]),
            {f"mentor-{self.event.pk}-adult": str(active_mentor.pk)},
            user=self.parent_user,
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            TravelMentorSignup.objects.filter(
                event=self.event, adult=active_mentor
            ).exists()
        )

    def test_lead_cannot_add_foreign_mentor_to_trip(self):
        foreign_organization = Organization.objects.create(
            name="Foreign Mentor Organization",
            slug="foreign-mentor-organization",
            subdomain="foreign-mentor-organization",
        )
        foreign_mentor = Adult.objects.create(
            organization=foreign_organization,
            legal_first_name="Foreign",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
        )

        response = self.tenant_post(
            reverse("travel:mentor_add", args=[self.program.pk, self.event.pk]),
            {f"mentor-{self.event.pk}-adult": str(foreign_mentor.pk)},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            TravelMentorSignup.objects.filter(
                event=self.event, adult=foreign_mentor
            ).exists()
        )

    def test_lead_cannot_add_foreign_parent_as_chaperone(self):
        foreign_parent = self.create_foreign_parent_linked_to_local_student()

        self.tenant_post(
            reverse("travel:chaperone_signup", args=[self.program.pk, self.event.pk]),
            {"adult": str(foreign_parent.pk)},
        )

        self.assertFalse(
            TravelParentChaperoneSignup.objects.filter(
                event=self.event, adult=foreign_parent
            ).exists()
        )

    def test_event_list_hides_foreign_adult_and_inline_child_links(self):
        foreign_departure, foreign_cost = self.create_foreign_event_children()
        foreign_organization = foreign_departure.organization
        foreign_mentor = Adult.objects.create(
            organization=foreign_organization,
            legal_first_name="Foreign",
            last_name="Mentor Roster",
            is_mentor=True,
        )
        foreign_parent = Adult.objects.create(
            organization=foreign_organization,
            legal_first_name="Foreign",
            last_name="Parent Roster",
            is_parent=True,
        )
        TravelMentorSignup.objects.create(
            organization=self.organization,
            adult=foreign_mentor,
            event=self.event,
        )
        TravelParentChaperoneSignup.objects.create(
            organization=self.organization,
            adult=foreign_parent,
            event=self.event,
        )

        response = self.tenant_get(reverse("travel:event_list", args=[self.program.pk]))

        self.assertEqual(response.status_code, 200)
        for foreign_value in (
            "Beta Private Departure",
            "Beta Private Cost",
            "Foreign Mentor Roster",
            "Foreign Parent Roster",
        ):
            with self.subTest(value=foreign_value):
                self.assertNotContains(response, foreign_value)

    def test_cancel_views_do_not_mutate_mismatched_adult_signups(self):
        foreign_organization = Organization.objects.create(
            name="Travel Cancel Beta",
            slug="travel-cancel-beta",
            subdomain="travel-cancel-beta",
        )
        foreign_parent_user = User.objects.create_user(
            username="foreign-parent-cancel", password="password"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=foreign_parent_user,
            organization=self.organization,
            role="Parent",
            is_active=True,
        )
        foreign_parent = Adult.objects.create(
            organization=foreign_organization,
            user=foreign_parent_user,
            legal_first_name="Foreign",
            last_name="Cancel Parent",
            is_parent=True,
        )
        parent_signup = TravelParentChaperoneSignup.objects.create(
            organization=self.organization,
            adult=foreign_parent,
            event=self.event,
        )
        foreign_mentor_user = User.objects.create_user(
            username="foreign-mentor-cancel", password="password"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=foreign_mentor_user,
            organization=self.organization,
            role="Mentor",
            is_active=True,
        )
        foreign_mentor = Adult.objects.create(
            organization=foreign_organization,
            user=foreign_mentor_user,
            legal_first_name="Foreign",
            last_name="Cancel Mentor",
            is_mentor=True,
        )
        mentor_signup = TravelMentorSignup.objects.create(
            organization=self.organization,
            adult=foreign_mentor,
            event=self.event,
        )

        with (
            patch(
                "travel.views.notify_parent_chaperone_signup_cancelled"
            ) as notify_parent,
            patch("travel.views.notify_mentor_signup_cancelled") as notify_mentor,
        ):
            self.tenant_post(
                reverse(
                    "travel:chaperone_cancel", args=[self.program.pk, self.event.pk]
                ),
                user=foreign_parent_user,
            )
            self.tenant_post(
                reverse("travel:mentor_cancel", args=[self.program.pk, self.event.pk]),
                user=foreign_mentor_user,
            )

        self.assertTrue(
            TravelParentChaperoneSignup.objects.filter(pk=parent_signup.pk).exists()
        )
        self.assertTrue(TravelMentorSignup.objects.filter(pk=mentor_signup.pk).exists())
        notify_parent.assert_not_called()
        notify_mentor.assert_not_called()

    def test_lead_actions_ignore_mismatched_adult_signups(self):
        foreign_organization = Organization.objects.create(
            name="Travel Lead Action Beta",
            slug="travel-lead-action-beta",
            subdomain="travel-lead-action-beta",
        )
        foreign_mentor = Adult.objects.create(
            organization=foreign_organization,
            legal_first_name="Foreign",
            last_name="Driver",
            is_mentor=True,
        )
        foreign_parent = Adult.objects.create(
            organization=foreign_organization,
            legal_first_name="Foreign",
            last_name="Removed Parent",
            is_parent=True,
        )
        mentor_signup = TravelMentorSignup.objects.create(
            organization=self.organization,
            adult=foreign_mentor,
            event=self.event,
        )
        parent_signup = TravelParentChaperoneSignup.objects.create(
            organization=self.organization,
            adult=foreign_parent,
            event=self.event,
        )

        self.tenant_post(
            reverse(
                "travel:mentor_driver_toggle",
                args=[self.program.pk, mentor_signup.pk],
            )
        )
        self.tenant_post(
            reverse(
                "travel:chaperone_remove",
                args=[self.program.pk, parent_signup.pk],
            )
        )

        mentor_signup.refresh_from_db()
        self.assertFalse(mentor_signup.is_driver)
        self.assertTrue(
            TravelParentChaperoneSignup.objects.filter(pk=parent_signup.pk).exists()
        )

    def test_mentor_signup_refuses_foreign_ownership_collision(self):
        foreign_organization = Organization.objects.create(
            name="Travel Mentor Collision Beta",
            slug="travel-mentor-collision-beta",
            subdomain="travel-mentor-collision-beta",
        )
        foreign_signup = TravelMentorSignup.objects.create(
            organization=foreign_organization,
            adult=self.lead_adult,
            event=self.event,
        )

        self.tenant_post(
            reverse("travel:mentor_signup", args=[self.program.pk, self.event.pk])
        )

        self.assertTrue(
            TravelMentorSignup.objects.filter(pk=foreign_signup.pk).exists()
        )
        self.assertFalse(
            TravelMentorSignup.objects.for_organization(self.organization)
            .filter(adult=self.lead_adult, event=self.event)
            .exists()
        )

    def test_parent_signup_refuses_foreign_ownership_collision(self):
        foreign_organization = Organization.objects.create(
            name="Travel Parent Collision Beta",
            slug="travel-parent-collision-beta",
            subdomain="travel-parent-collision-beta",
        )
        foreign_signup = TravelParentChaperoneSignup.objects.create(
            organization=foreign_organization,
            adult=self.parent,
            event=self.event,
        )

        self.tenant_post(
            reverse("travel:chaperone_signup", args=[self.program.pk, self.event.pk]),
            {"adult": str(self.parent.pk)},
        )

        self.assertTrue(
            TravelParentChaperoneSignup.objects.filter(pk=foreign_signup.pk).exists()
        )
        self.assertFalse(
            TravelParentChaperoneSignup.objects.for_organization(self.organization)
            .filter(adult=self.parent, event=self.event)
            .exists()
        )

    def test_signup_paths_do_not_attach_foreign_departure(self):
        foreign_organization = Organization.objects.create(
            name="Travel Departure Beta",
            slug="travel-departure-beta",
            subdomain="travel-departure-beta",
        )
        foreign_departure = TravelDeparture.objects.create(
            organization=foreign_organization,
            event=self.event,
            label="Beta Private Departure",
            leave_date=self.start_date + timedelta(days=1),
        )

        self.tenant_post(
            reverse("travel:mentor_signup", args=[self.program.pk, self.event.pk]),
            {"departure": str(foreign_departure.pk)},
        )
        self.tenant_post(
            reverse("travel:chaperone_signup", args=[self.program.pk, self.event.pk]),
            {
                "adult": str(self.parent.pk),
                "departure": str(foreign_departure.pk),
            },
        )
        self.tenant_post(
            reverse("travel:signup", args=[self.program.pk, self.event.pk]),
            {
                "student": str(self.student.pk),
                "departure": str(foreign_departure.pk),
            },
            user=self.lead,
        )

        mentor_signup = TravelMentorSignup.objects.for_organization(
            self.organization
        ).get(event=self.event, adult=self.lead_adult)
        parent_signup = TravelParentChaperoneSignup.objects.for_organization(
            self.organization
        ).get(event=self.event, adult=self.parent)
        self.assertEqual(mentor_signup.departure, self.departure)
        self.assertEqual(parent_signup.departure, self.departure)
        self.assertFalse(
            TravelSignup.objects.for_organization(self.organization)
            .filter(event=self.event, student=self.student)
            .exists()
        )

    def test_lead_creates_trip_departures_and_costs_with_tenant_ownership(self):
        payload = {
            "name": "New Ownership Trip",
            "start_date": self.start_date.isoformat(),
            "end_date": (self.start_date + timedelta(days=2)).isoformat(),
            "location_name": "Local Venue",
            "location_address": "1 Local Street",
            "description": "",
            "departures-TOTAL_FORMS": "1",
            "departures-INITIAL_FORMS": "0",
            "departures-MIN_NUM_FORMS": "0",
            "departures-MAX_NUM_FORMS": "1000",
            "departures-0-label": "New Bus",
            "departures-0-leave_date": self.start_date.isoformat(),
            "departures-0-leave_time": "",
            "departures-0-meeting_location": "School",
            "departures-0-notes": "",
            "costs-TOTAL_FORMS": "1",
            "costs-INITIAL_FORMS": "0",
            "costs-MIN_NUM_FORMS": "0",
            "costs-MAX_NUM_FORMS": "1000",
            "costs-0-name": "New Hotel",
            "costs-0-amount": "125.00",
            "costs-0-unit_label": "per person",
            "costs-0-notes": "",
            "costs-0-sort_order": "0",
        }

        response = self.tenant_post(
            reverse("travel:event_create", args=[self.program.pk]), payload
        )

        self.assertEqual(response.status_code, 302)
        event = TravelEvent.objects.for_organization(self.organization).get(
            name="New Ownership Trip"
        )
        self.assertEqual(event.program, self.program)
        self.assertEqual(
            event.departures.get(label="New Bus").organization, self.organization
        )
        self.assertEqual(
            event.cost_options.get(name="New Hotel").organization, self.organization
        )

    def test_event_update_assigns_organization_only_to_new_inline_rows(self):
        payload = {
            "name": self.event.name,
            "start_date": self.start_date.isoformat(),
            "end_date": (self.start_date + timedelta(days=2)).isoformat(),
            "location_name": "",
            "location_address": "",
            "description": "",
            "departures-TOTAL_FORMS": "2",
            "departures-INITIAL_FORMS": "1",
            "departures-MIN_NUM_FORMS": "0",
            "departures-MAX_NUM_FORMS": "1000",
            "departures-0-id": str(self.departure.pk),
            "departures-0-label": "Existing Bus Updated",
            "departures-0-leave_date": self.start_date.isoformat(),
            "departures-0-leave_time": "",
            "departures-0-meeting_location": "School",
            "departures-0-notes": "",
            "departures-1-label": "Added Bus",
            "departures-1-leave_date": self.start_date.isoformat(),
            "departures-1-leave_time": "",
            "departures-1-meeting_location": "Other Location",
            "departures-1-notes": "",
            "costs-TOTAL_FORMS": "2",
            "costs-INITIAL_FORMS": "1",
            "costs-MIN_NUM_FORMS": "0",
            "costs-MAX_NUM_FORMS": "1000",
            "costs-0-id": str(self.cost_option.pk),
            "costs-0-name": "Existing Hotel Updated",
            "costs-0-amount": "100.00",
            "costs-0-unit_label": "per person",
            "costs-0-notes": "",
            "costs-0-sort_order": "0",
            "costs-1-name": "Added Cost",
            "costs-1-amount": "25.00",
            "costs-1-unit_label": "per person",
            "costs-1-notes": "",
            "costs-1-sort_order": "1",
        }

        response = self.tenant_post(
            reverse("travel:event_edit", args=[self.program.pk, self.event.pk]),
            payload,
        )

        self.assertEqual(response.status_code, 302)
        self.departure.refresh_from_db()
        self.cost_option.refresh_from_db()
        self.assertEqual(self.departure.organization, self.organization)
        self.assertEqual(self.cost_option.organization, self.organization)
        self.assertEqual(
            self.event.departures.get(label="Added Bus").organization,
            self.organization,
        )
        self.assertEqual(
            self.event.cost_options.get(name="Added Cost").organization,
            self.organization,
        )

    def test_mentor_and_parent_signup_creation_is_tenant_owned_and_idempotent(self):
        mentor_url = reverse(
            "travel:mentor_signup", args=[self.program.pk, self.event.pk]
        )
        parent_url = reverse(
            "travel:chaperone_signup", args=[self.program.pk, self.event.pk]
        )

        self.tenant_post(mentor_url)
        self.tenant_post(mentor_url)
        self.tenant_post(parent_url, user=self.parent_user)
        self.tenant_post(parent_url, user=self.parent_user)

        mentor_signup = TravelMentorSignup.objects.for_organization(
            self.organization
        ).get(event=self.event, adult__user=self.lead)
        parent_signup = TravelParentChaperoneSignup.objects.for_organization(
            self.organization
        ).get(event=self.event, adult=self.parent)
        self.assertEqual(mentor_signup.organization, self.organization)
        self.assertEqual(parent_signup.organization, self.organization)
        self.assertEqual(
            TravelMentorSignup.objects.for_organization(self.organization)
            .filter(event=self.event)
            .count(),
            1,
        )
        self.assertEqual(
            TravelParentChaperoneSignup.objects.for_organization(self.organization)
            .filter(event=self.event)
            .count(),
            1,
        )
