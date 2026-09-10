from datetime import timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Program,
    ProgramFeature,
    School,
    Student,
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

        self.lead = User.objects.create_superuser(
            username="lead", email="lead@example.com", password="password"
        )  # nosec B106

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
            event=self.event, name="Van", amount="50.00"
        )

    def tearDown(self):
        mail.outbox = []

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

    def test_mentor_sees_trips_read_only(self):
        self.client.login(username="mentor", password="password")  # nosec B106
        url = reverse("travel:event_list", args=[self.program.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["can_manage"])
        self.assertFalse(resp.context["can_add"])
        html = resp.content.decode()
        self.assertNotIn(">Edit</a>", html)
        self.assertNotIn(">Delete</a>", html)
        self.assertNotIn("Add Trip", html)

    def test_list_view_accessible_to_all_roles(self):
        for username in ("student", "parent", "mentor", "lead"):
            self.client.login(username=username, password="password")  # nosec B106
            url = reverse("travel:event_list", args=[self.program.id])
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, msg=username)

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
        self.client.login(username="student", password="password")  # nosec B106
        url = reverse("travel:signup", args=[self.program.id, self.event.id])
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

    def test_lead_can_add_student_still_needs_approval(self):
        self.client.login(username="lead", password="password")  # nosec B106
        url = reverse("travel:signup", args=[self.program.id, self.event.id])
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
        resp = self.client.post(url)
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
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        signup.refresh_from_db()
        self.assertEqual(signup.status, TravelSignup.INTERESTED)

    def test_lead_cannot_approve_signup(self):
        signup = TravelSignup.objects.create(student=self.student, event=self.event)
        self.client.login(username="lead", password="password")  # nosec B106
        url = reverse("travel:signup_approve", args=[self.program.id, signup.id])
        self.client.post(url)
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
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        signup.refresh_from_db()
        self.assertEqual(signup.status, TravelSignup.INTERESTED)

        # With the checkbox -> approved and acked.
        resp = self.client.post(url, {"permission_acknowledged": "1"})
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
