from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from organizations.models import Organization, UserOrganizationMembership
from outreach.forms import OutreachEventForm, OutreachManageSignupsForm
from outreach.models import (
    OutreachEvent,
    OutreachLocation,
    OutreachShift,
    OutreachSignup,
)
from programs.models import Enrollment, Program, ProgramFeature, Student


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
