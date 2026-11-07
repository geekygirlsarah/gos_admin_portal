from datetime import date, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from organizations.models import Organization, UserOrganizationMembership
from outreach.forms import (
    OutreachEventForm,
    OutreachManageMentorSignupsForm,
    OutreachManageSignupsForm,
)
from outreach.models import (
    OutreachEvent,
    OutreachLocation,
    OutreachMentorSignup,
    OutreachShift,
    OutreachSignup,
)
from programs.models import Adult, Enrollment, Program, ProgramFeature, Student


class OutreachManagementTenantIsolationTests(TestCase):
    def setUp(self):
        self.alpha = Organization.objects.create(
            name="Outreach Alpha", slug="outreach-alpha", subdomain="outreach-alpha"
        )
        self.beta = Organization.objects.create(
            name="Outreach Beta", slug="outreach-beta", subdomain="outreach-beta"
        )
        self.feature, _ = ProgramFeature.objects.get_or_create(
            key="outreach", defaults={"name": "Outreach"}
        )
        self.alpha_program = Program.objects.create(
            organization=self.alpha, name="Alpha Program", active=True
        )
        self.alpha_program.features.add(self.feature)
        self.beta_program = Program.objects.create(
            organization=self.beta, name="Beta Program", active=True
        )
        self.beta_program.features.add(self.feature)
        self.user = get_user_model().objects.create_user(
            username="outreach-alpha-lead", password="password123"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=self.user, organization=self.alpha, role="LeadMentor", is_active=True
        )
        self.client.force_login(self.user)
        self.alpha_host = "outreach-alpha.localhost"
        self.beta_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="BetaPrivate",
            last_name="Student",
        )
        self.alpha_event, self.alpha_shift = self.make_event(
            self.alpha, self.alpha_program, "Alpha Event"
        )
        self.beta_event, self.beta_shift = self.make_event(
            self.beta, self.beta_program, "Beta Private Event"
        )
        self.beta_signup = OutreachSignup.objects.create(
            organization=self.beta,
            shift=self.beta_shift,
            student=self.beta_student,
            role=OutreachSignup.CHAMPION,
        )

    def make_event(self, organization, program, name):
        event = OutreachEvent.objects.create(
            organization=organization,
            program=program,
            name=name,
            location_name=f"{name} Location",
            location_address="1 Example Street",
        )
        shift = OutreachShift.objects.create(
            organization=organization,
            event=event,
            date=timezone.localdate() + timedelta(days=1),
            start_time=time(10),
            end_time=time(12),
        )
        return event, shift

    def tenant_get(self, path):
        return self.client.get(path, HTTP_HOST=self.alpha_host)

    def tenant_post(self, path, data=None):
        return self.client.post(path, data=data or {}, HTTP_HOST=self.alpha_host)

    def test_event_editor_hides_foreign_shift_rows(self):
        foreign_date = timezone.localdate() + timedelta(days=3)
        OutreachShift.objects.create(
            organization=self.beta,
            event=self.alpha_event,
            date=foreign_date,
            start_time=time(14),
            end_time=time(16),
            max_champions=17,
        )

        response = self.tenant_get(
            reverse(
                "outreach:event_edit",
                args=[self.alpha_program.pk, self.alpha_event.pk],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            f'value="{foreign_date.isoformat()}"'.encode(), response.content
        )
        self.assertNotIn(b'value="17"', response.content)

    def test_event_editor_cannot_delete_foreign_shift_rows(self):
        foreign_shift = OutreachShift.objects.create(
            organization=self.beta,
            event=self.alpha_event,
            date=timezone.localdate() + timedelta(days=3),
            start_time=time(14),
            end_time=time(16),
            max_champions=17,
        )
        payload = {
            "name": self.alpha_event.name,
            "saved_location": "",
            "location_name": self.alpha_event.location_name,
            "location_address": self.alpha_event.location_address,
            "description": self.alpha_event.description,
            "shifts-TOTAL_FORMS": "2",
            "shifts-INITIAL_FORMS": "2",
            "shifts-MIN_NUM_FORMS": "0",
            "shifts-MAX_NUM_FORMS": "1000",
            "shifts-0-id": str(self.alpha_shift.pk),
            "shifts-0-date": self.alpha_shift.date.isoformat(),
            "shifts-0-start_time": self.alpha_shift.start_time.strftime("%H:%M:%S"),
            "shifts-0-end_time": self.alpha_shift.end_time.strftime("%H:%M:%S"),
            "shifts-0-max_champions": str(self.alpha_shift.max_champions),
            "shifts-0-max_helpers": str(self.alpha_shift.max_helpers),
            "shifts-1-id": str(foreign_shift.pk),
            "shifts-1-date": foreign_shift.date.isoformat(),
            "shifts-1-start_time": foreign_shift.start_time.strftime("%H:%M:%S"),
            "shifts-1-end_time": foreign_shift.end_time.strftime("%H:%M:%S"),
            "shifts-1-max_champions": str(foreign_shift.max_champions),
            "shifts-1-max_helpers": str(foreign_shift.max_helpers),
            "shifts-1-DELETE": "on",
        }

        self.tenant_post(
            reverse(
                "outreach:event_edit",
                args=[self.alpha_program.pk, self.alpha_event.pk],
            ),
            payload,
        )

        self.assertTrue(OutreachShift.objects.filter(pk=foreign_shift.pk).exists())
        foreign_shift.refresh_from_db()
        self.assertEqual(foreign_shift.organization, self.beta)

    def test_signup_management_does_not_reassign_foreign_signup_collision(self):
        alpha_student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Alpha",
            last_name="Collision Student",
        )
        Enrollment.objects.create(
            organization=self.alpha,
            student=alpha_student,
            program=self.alpha_program,
            active=True,
        )
        foreign_signup = OutreachSignup.objects.create(
            organization=self.beta,
            shift=self.alpha_shift,
            student=alpha_student,
            role=OutreachSignup.CHAMPION,
        )

        self.tenant_post(
            reverse(
                "outreach:shift_manage_signups",
                args=[self.alpha_program.pk, self.alpha_shift.pk],
            ),
            {"champions": [], "helpers": [str(alpha_student.pk)]},
        )

        foreign_signup.refresh_from_db()
        self.assertEqual(foreign_signup.organization, self.beta)
        self.assertEqual(foreign_signup.role, OutreachSignup.CHAMPION)
        self.assertFalse(
            OutreachSignup.objects.for_organization(self.alpha)
            .filter(shift=self.alpha_shift, student=alpha_student)
            .exists()
        )

    def test_mentor_signup_collision_does_not_notify_or_change_foreign_row(self):
        alpha_mentor = Adult.objects.create(
            organization=self.alpha,
            legal_first_name="Alpha",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
        )
        foreign_signup = OutreachMentorSignup.objects.create(
            organization=self.beta,
            shift=self.alpha_shift,
            adult=alpha_mentor,
        )
        form = OutreachManageMentorSignupsForm(
            data={"mentors": [str(alpha_mentor.pk)]},
            shift=self.alpha_shift,
        )

        with patch("outreach.services.notify_mentor_outreach_signup") as notify:
            is_valid = form.is_valid()
            if is_valid:
                form.save()

        self.assertFalse(notify.called)
        self.assertFalse(is_valid)
        foreign_signup.refresh_from_db()
        self.assertEqual(foreign_signup.organization, self.beta)

    def test_membership_only_lead_sees_local_event_controls(self):
        response = self.tenant_get(
            reverse("outreach:event_list", args=[self.alpha_program.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_add"])
        self.assertTrue(response.context["viewer_is_mentor"])

    def test_foreign_program_event_list_and_student_stats_are_not_visible(self):
        paths = (
            reverse("outreach:event_list", args=[self.beta_program.pk]),
            reverse("outreach:student_stats", args=[self.beta_program.pk]),
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.tenant_get(path)
                self.assertNotEqual(response.status_code, 200)
                self.assertNotIn(b"Beta Private Event", response.content)
                self.assertNotIn(b"BetaPrivate", response.content)

    def test_foreign_shift_signup_management_does_not_change_signups(self):
        url = reverse(
            "outreach:shift_manage_signups",
            args=[self.beta_program.pk, self.beta_shift.pk],
        )

        response = self.tenant_post(url, {"champions": [], "helpers": []})

        self.assertNotEqual(response.status_code, 200)
        self.assertTrue(OutreachSignup.objects.filter(pk=self.beta_signup.pk).exists())

    def test_foreign_saved_location_cannot_be_edited_or_deleted(self):
        location = OutreachLocation.objects.create(
            organization=self.beta,
            name="Beta Private Venue",
            address="Private Beta Address",
        )
        edit_url = reverse(
            "outreach:location_edit",
            kwargs={"program_id": self.alpha_program.pk, "pk": location.pk},
        )
        delete_url = reverse(
            "outreach:location_delete",
            kwargs={"program_id": self.alpha_program.pk, "pk": location.pk},
        )

        edit_response = self.tenant_post(
            edit_url, {"name": "Changed Venue", "address": "Changed Address"}
        )
        delete_response = self.tenant_post(delete_url)

        self.assertNotEqual(edit_response.status_code, 200)
        self.assertNotEqual(delete_response.status_code, 200)
        self.assertTrue(OutreachLocation.objects.filter(pk=location.pk).exists())
        location.refresh_from_db()
        self.assertEqual(location.name, "Beta Private Venue")

    def test_event_form_rejects_foreign_saved_location(self):
        location = OutreachLocation.objects.create(
            organization=self.beta,
            name="Beta Picker Venue",
            address="Private Beta Address",
        )
        form = OutreachEventForm(
            data={
                "name": "Alpha Event",
                "saved_location": location.pk,
                "location_name": location.name,
                "location_address": location.address,
                "description": "",
            },
            organization=self.alpha,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("saved_location", form.errors)

    def test_signup_form_rejects_foreign_student_id(self):
        form = OutreachManageSignupsForm(
            data={"champions": [self.beta_student.pk], "helpers": []},
            shift=self.alpha_shift,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("champions", form.errors)

    def test_lead_can_operate_checkin_after_champion_window_ends(self):
        self.alpha_shift.date = date.today() - timedelta(days=2)
        self.alpha_shift.save(update_fields=["date"])
        signup = OutreachSignup.objects.create(
            organization=self.alpha,
            shift=self.alpha_shift,
            student=Student.objects.create(
                organization=self.alpha,
                legal_first_name="Alpha",
                last_name="Student",
            ),
            role=OutreachSignup.CHAMPION,
            checked_in_at=timezone.now() - timedelta(days=2),
            checked_out_at=timezone.now() - timedelta(days=2),
        )
        response = self.tenant_get(
            reverse(
                "outreach:shift_check_in",
                args=[self.alpha_program.pk, self.alpha_shift.pk],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_operate"])
        self.assertIsNotNone(signup.pk)

    def test_walk_up_checkin_uses_tenant_organization_and_is_idempotent(self):
        alpha_student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Alpha",
            last_name="Walkup",
        )
        Enrollment.objects.create(
            organization=self.alpha,
            student=alpha_student,
            program=self.alpha_program,
            active=True,
        )
        url = reverse(
            "outreach:shift_check_in",
            args=[self.alpha_program.pk, self.alpha_shift.pk],
        )
        data = {"action": "walk_up", "student_id": alpha_student.pk}

        self.tenant_post(url, data)
        signup = OutreachSignup.objects.for_organization(self.alpha).get(
            shift=self.alpha_shift, student=alpha_student
        )
        original_check_in = signup.checked_in_at
        self.assertEqual(signup.organization, self.alpha)

        self.tenant_post(url, data)
        self.assertEqual(
            OutreachSignup.objects.for_organization(self.alpha)
            .filter(shift=self.alpha_shift, student=alpha_student)
            .count(),
            1,
        )
        signup.refresh_from_db()
        self.assertEqual(signup.checked_in_at, original_check_in)

        self.tenant_post(url, {"action": "walk_up", "student_id": self.beta_student.pk})
        self.assertFalse(
            OutreachSignup.objects.filter(
                shift=self.alpha_shift, student=self.beta_student
            ).exists()
        )
