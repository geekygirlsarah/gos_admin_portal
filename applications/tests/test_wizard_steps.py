"""Tests for the application wizard Steps 5-9 (student info through confirm)."""

from __future__ import annotations

import datetime

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from programs.models import Adult, Program, School, Student


def _verified(**kwargs):
    """Convenience: create an application that has cleared Steps 1-4."""
    defaults = dict(
        applicant_type=Application.Type.PARENT,
        email="parent@example.com",
        current_step=5,
        email_verified_at=timezone.now(),
        status=Application.Status.EMAIL_VERIFIED,
    )
    defaults.update(kwargs)
    return Application.objects.create(**defaults)


class Step5StudentInfoTests(TestCase):
    def setUp(self):
        School.objects.get_or_create(name="Pittsburgh High")
        today = timezone.localdate()
        self.program = Program.objects.create(
            name="Spring 2030",
            start_date=today + datetime.timedelta(days=60),
            end_date=today + datetime.timedelta(days=120),
            active=True,
        )
        mail.outbox = []

    def test_step5_redirects_to_step3_if_email_not_verified(self):
        app = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            current_step=5,
        )
        response = self.client.get(
            reverse("apply_step5", kwargs={"app_id": app.application_id})
        )
        self.assertRedirects(
            response,
            reverse("apply_step3", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    def test_step5_get_prefills_for_existing_student_email(self):
        Student.objects.create(
            legal_first_name="Ada",
            last_name="Lovelace",
            personal_email="ada@example.com",
            address="123 Science Way",
            city="London",
            state="PA",
            zip_code="SW1",
        )
        app = _verified(
            applicant_type=Application.Type.STUDENT, email="ada@example.com"
        )
        response = self.client.get(
            reverse("apply_step5", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="Ada"')
        self.assertContains(response, 'value="Lovelace"')
        self.assertContains(response, 'value="123 Science Way"')
        self.assertContains(response, 'value="London"')
        self.assertContains(response, 'value="SW1"')

    def test_step5_post_saves_data_and_advances_to_step6(self):
        app = _verified()
        response = self.client.post(
            reverse("apply_step5", kwargs={"app_id": app.application_id}),
            {
                "legal_first_name": "Grace",
                "preferred_first_name": "",
                "last_name": "Hopper",
                "pronouns": "",
                "personal_email": "grace@example.com",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "phone_number": "",
                "school_name": "Pittsburgh High",
                "grade": "9",
                "graduation_year": "",
                "tshirt_size": "M",
                "allergies": "",
                "dietary_restrictions": "",
                "medical_notes": "",
                "date_of_birth": "2010-01-01",
            },
        )
        self.assertRedirects(
            response,
            reverse("apply_step6", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
        app.refresh_from_db()
        self.assertEqual(
            app.data.get("step5-student", {}).get("legal_first_name"), "Grace"
        )
        self.assertGreaterEqual(app.current_step, 6)

    def test_step5_picker_shown_for_parent_with_existing_children(self):
        adult = Adult.objects.create(
            legal_first_name="Pat",
            last_name="Parent",
            personal_email="parent@example.com",
            is_parent=True,
        )
        student_a = Student.objects.create(
            preferred_first_name="Anna",
            last_name="Smith",
            primary_contact=adult,
        )
        Student.objects.create(
            legal_first_name="Bea",
            last_name="Smith",
            primary_contact=adult,
        )
        app = _verified(email="parent@example.com")
        response = self.client.get(
            reverse("apply_step5", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Anna")
        self.assertContains(response, "Bea")
        response2 = self.client.post(
            reverse("apply_step5", kwargs={"app_id": app.application_id}),
            {"student": str(student_a.pk), "_pick_student": "1"},
        )
        self.assertEqual(response2.status_code, 200)
        self.assertContains(response2, 'value="Anna"')


class Step6ExperienceTests(TestCase):
    def setUp(self):
        self.app = _verified(current_step=6)

    def test_step6_get_renders(self):
        response = self.client.get(
            reverse("apply_step6", kwargs={"app_id": self.app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Experience and interest")

    def test_step6_post_saves_and_advances(self):
        response = self.client.post(
            reverse("apply_step6", kwargs={"app_id": self.app.application_id}),
            {
                "interest_reason": "I love robots",
                "hoped_gains": "Knowledge",
                "prior_robotics_experience": "None",
                "referral_source": "Friend",
            },
        )
        self.assertRedirects(
            response,
            reverse("apply_step7", kwargs={"app_id": self.app.application_id}),
            fetch_redirect_response=False,
        )
        self.app.refresh_from_db()
        self.assertEqual(
            self.app.data["step6-experience"]["interest_reason"], "I love robots"
        )
        self.assertEqual(self.app.current_step, 7)


class Step7PrimaryParentTests(TestCase):
    def setUp(self):
        mail.outbox = []

    def test_student_initiated_no_existing_parent_shows_handoff(self):
        app = _verified(
            applicant_type=Application.Type.STUDENT,
            email="kid@example.com",
            current_step=7,
        )
        response = self.client.get(
            reverse("apply_step7", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email my adult contact")

    def test_student_handoff_post_emails_parent_and_redirects_to_start(self):
        app = _verified(
            applicant_type=Application.Type.STUDENT,
            email="kid@example.com",
            current_step=7,
        )
        response = self.client.post(
            reverse("apply_step7", kwargs={"app_id": app.application_id}),
            {"parent_email": "guardian@example.com"},
        )
        self.assertRedirects(
            response, reverse("apply_start"), fetch_redirect_response=False
        )
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.AWAITING_PARENT)
        self.assertEqual(
            app.data.get("step7_handoff", {}).get("parent_email"),
            "guardian@example.com",
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("guardian@example.com", mail.outbox[0].to)
        self.assertIn(app.application_id, mail.outbox[0].body)

    def test_parent_with_existing_adult_record_prefills_form(self):
        Adult.objects.create(
            legal_first_name="Pat",
            last_name="Parent",
            personal_email="parent@example.com",
            phone_number="555-444-1212",
            phone_type="cell",
            is_parent=True,
        )
        app = _verified(email="parent@example.com", current_step=7)
        response = self.client.get(
            reverse("apply_step7", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="Pat"')
        self.assertContains(response, 'value="Parent"')
        self.assertContains(response, "555-444-1212")

    def test_parent_form_post_saves_and_advances_to_step8(self):
        app = _verified()
        response = self.client.post(
            reverse("apply_step7", kwargs={"app_id": app.application_id}),
            {
                "legal_first_name": "Pat",
                "last_name": "Parent",
                "relationship_to_student": "parent",
                "email": "parent@example.com",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "phone_number": "555-444-1212",
                "phone_type": "cell",
            },
        )
        self.assertRedirects(
            response,
            reverse("apply_step8", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
        app.refresh_from_db()
        self.assertEqual(
            app.data.get("step7-primaryparent", {}).get("legal_first_name"), "Pat"
        )
        self.assertGreaterEqual(app.current_step, 8)


class Step8SecondaryParentTests(TestCase):
    def test_secondary_parent_is_required(self):
        app = _verified(current_step=8)
        response = self.client.post(
            reverse("apply_step8", kwargs={"app_id": app.application_id}),
            {"skip": "1"},
        )
        self.assertEqual(response.status_code, 200)
        app.refresh_from_db()
        self.assertNotIn("step8-secondaryparent", app.data or {})

    def test_filled_form_saves_and_advances(self):
        app = _verified(current_step=8)
        response = self.client.post(
            reverse("apply_step8", kwargs={"app_id": app.application_id}),
            {
                "legal_first_name": "Sam",
                "last_name": "Spouse",
                "relationship_to_student": "guardian",
                "email": "",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "phone_number": "555-444-1212",
                "phone_type": "cell",
            },
        )
        self.assertRedirects(
            response,
            reverse("apply_step9", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
        app.refresh_from_db()
        self.assertEqual(
            app.data.get("step8-secondaryparent", {}).get("legal_first_name"), "Sam"
        )


class Step9ConfirmTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.program = Program.objects.create(
            name="Spring 2030",
            start_date=today + datetime.timedelta(days=60),
            end_date=today + datetime.timedelta(days=120),
            active=True,
        )
        mail.outbox = []

    def _verified_with_data(self):
        return _verified(
            program=self.program,
            current_step=9,
            data={
                "step5-student": {"legal_first_name": "Grace", "last_name": "Hopper"},
                "step7-primaryparent": {
                    "legal_first_name": "Pat",
                    "last_name": "Parent",
                    "email": "parent@example.com",
                    "email_updates": True,
                },
                "step8-secondaryparent": {
                    "legal_first_name": "Sam",
                    "last_name": "Spouse",
                    "relationship_to_student": "guardian",
                },
            },
        )

    def test_get_renders_review_with_collected_data(self):
        app = self._verified_with_data()
        response = self.client.get(
            reverse("apply_step9", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Grace")
        self.assertContains(response, "Pat")
        self.assertContains(response, self.program.name)

    def test_post_without_confirm_stays_on_page(self):
        app = self._verified_with_data()
        response = self.client.post(
            reverse("apply_step9", kwargs={"app_id": app.application_id}),
            {},
        )
        self.assertEqual(response.status_code, 200)
        app.refresh_from_db()
        self.assertNotEqual(app.status, Application.Status.SUBMITTED)

    def test_post_confirms_and_submits_application(self):
        app = self._verified_with_data()
        response = self.client.post(
            reverse("apply_step9", kwargs={"app_id": app.application_id}),
            {"confirm": "on"},
        )
        self.assertRedirects(
            response,
            reverse("apply_submitted", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.SUBMITTED)
        self.assertIsNotNone(app.submitted_at)
        self.assertEqual(len(mail.outbox), 2)
        recipients = {addr for m in mail.outbox for addr in m.to}
        self.assertIn(app.email, recipients)
        self.assertIn("parent@example.com", recipients)
        self.assertTrue(
            any("leads@girlsofsteelrobotics.org" in addr for addr in recipients)
        )
        confirm_msgs = [
            m for m in mail.outbox if "leads@girlsofsteelrobotics.org" not in m.to
        ]
        self.assertEqual(len(confirm_msgs), 1)
        confirm = confirm_msgs[0]
        self.assertIn(app.email, confirm.to)
        self.assertIn("parent@example.com", confirm.to)

    def test_submit_sends_only_parent_when_student_has_no_email(self):
        app = _verified(
            program=self.program,
            current_step=9,
            email="parent@example.com",
            data={
                "step5-student": {"legal_first_name": "Ada", "last_name": "Lovelace"},
                "step7-primaryparent": {
                    "legal_first_name": "Pat",
                    "last_name": "Parent",
                    "email": "parent@example.com",
                    "email_updates": True,
                },
                "step8-secondaryparent": {
                    "legal_first_name": "Sam",
                    "last_name": "Spouse",
                    "relationship_to_student": "guardian",
                },
            },
        )
        self.client.post(
            reverse("apply_step9", kwargs={"app_id": app.application_id}),
            {"confirm": "on"},
        )
        confirm_msgs = [
            m for m in mail.outbox if "leads@girlsofsteelrobotics.org" not in m.to
        ]
        self.assertEqual(len(confirm_msgs), 1)
        self.assertEqual(confirm_msgs[0].to, ["parent@example.com"])

    def test_submitted_page_renders(self):
        app = self._verified_with_data()
        app.status = Application.Status.SUBMITTED
        app.submitted_at = timezone.now()
        app.current_step = 10
        app.save()
        response = self.client.get(
            reverse("apply_submitted", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, app.application_id)


class Step5ValidationTests(TestCase):
    """Step 5 form validation: birthdate, grade, and t-shirt field."""

    def setUp(self):
        School.objects.get_or_create(name="Pittsburgh High")
        self.app = Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            email="student@example.com",
            current_step=5,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
        )

    def test_birthdate_required(self):
        response = self.client.post(
            reverse("apply_step5", kwargs={"app_id": self.app.application_id}),
            {
                "legal_first_name": "Grace",
                "last_name": "Hopper",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "tshirt_size": "M",
                "school_name": "Pittsburgh High",
                "grade": "9",
            },
            follow=True,
        )
        self.assertContains(response, "This field is required")

    def test_birthdate_future_date_invalid(self):
        future_date = timezone.localdate() + datetime.timedelta(days=1)
        response = self.client.post(
            reverse("apply_step5", kwargs={"app_id": self.app.application_id}),
            {
                "legal_first_name": "Grace",
                "last_name": "Hopper",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "tshirt_size": "M",
                "date_of_birth": future_date.strftime("%Y-%m-%d"),
                "school_name": "Pittsburgh High",
                "grade": "9",
            },
            follow=True,
        )
        self.assertContains(response, "Date of birth cannot be in the future")

    def test_birthdate_19_older_invalid(self):
        dob = timezone.localdate() - datetime.timedelta(days=20 * 365)
        response = self.client.post(
            reverse("apply_step5", kwargs={"app_id": self.app.application_id}),
            {
                "legal_first_name": "Grace",
                "last_name": "Hopper",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "tshirt_size": "M",
                "date_of_birth": dob.strftime("%Y-%m-%d"),
                "school_name": "Pittsburgh High",
                "grade": "9",
            },
            follow=True,
        )
        self.assertContains(response, "must be 18 or younger")

    def test_birthdate_young_allowed_with_confirmation(self):
        dob = timezone.localdate() - datetime.timedelta(days=4 * 365)
        response = self.client.post(
            reverse("apply_step5", kwargs={"app_id": self.app.application_id}),
            {
                "legal_first_name": "Grace",
                "last_name": "Hopper",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "tshirt_size": "M",
                "date_of_birth": dob.strftime("%Y-%m-%d"),
                "school_name": "Pittsburgh High",
                "grade": "9",
            },
            follow=True,
        )
        self.assertContains(response, "seems a bit young")
        self.assertContains(response, "I confirm this birthdate is correct")

        response = self.client.post(
            reverse("apply_step5", kwargs={"app_id": self.app.application_id}),
            {
                "legal_first_name": "Grace",
                "last_name": "Hopper",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "tshirt_size": "M",
                "date_of_birth": dob.strftime("%Y-%m-%d"),
                "confirm_age": "on",
                "school_name": "Pittsburgh High",
                "grade": "9",
            },
            follow=True,
        )
        self.assertRedirects(
            response, reverse("apply_step6", kwargs={"app_id": self.app.application_id})
        )


class GradeValidationTests(TestCase):
    def setUp(self):
        self.school, _ = School.objects.get_or_create(name="Pittsburgh High")
        self.program = Program.objects.create(
            name="Summer Camp",
            start_date=timezone.now().date() + datetime.timedelta(days=30),
            end_date=timezone.now().date() + datetime.timedelta(days=35),
            grade_range_start=4,
            grade_range_end=6,
            active=True,
        )
        self.app = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            program=self.program,
            current_step=5,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
        )

    def test_grade_within_range_no_warning(self):
        dob = timezone.localdate() - datetime.timedelta(days=10 * 365)
        response = self.client.post(
            reverse("apply_step5", kwargs={"app_id": self.app.application_id}),
            {
                "legal_first_name": "Grace",
                "last_name": "Hopper",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "date_of_birth": dob.strftime("%Y-%m-%d"),
                "school_name": self.school.name,
                "grade": "5",
            },
            follow=True,
        )
        self.assertNotContains(
            response, "seems to be outside the recommended grade range"
        )
        self.assertRedirects(
            response, reverse("apply_step6", kwargs={"app_id": self.app.application_id})
        )

    def test_grade_outside_range_requires_confirmation(self):
        dob = timezone.localdate() - datetime.timedelta(days=13 * 365)
        response = self.client.post(
            reverse("apply_step5", kwargs={"app_id": self.app.application_id}),
            {
                "legal_first_name": "Grace",
                "last_name": "Hopper",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "date_of_birth": dob.strftime("%Y-%m-%d"),
                "school_name": self.school.name,
                "grade": "8",
            },
            follow=True,
        )
        self.assertContains(response, "seems to be outside the recommended grade range")
        self.assertContains(response, "I confirm this grade is correct")

        response = self.client.post(
            reverse("apply_step5", kwargs={"app_id": self.app.application_id}),
            {
                "legal_first_name": "Grace",
                "last_name": "Hopper",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "date_of_birth": dob.strftime("%Y-%m-%d"),
                "school_name": self.school.name,
                "grade": "8",
                "confirm_grade": "on",
            },
            follow=True,
        )
        self.assertRedirects(
            response, reverse("apply_step6", kwargs={"app_id": self.app.application_id})
        )


class TshirtFieldTests(TestCase):
    def test_tshirt_field_presence_by_default(self):
        from applications.forms import StudentInfoForm

        form = StudentInfoForm()
        self.assertIn("tshirt_size", form.fields)

    def test_tshirt_field_removable(self):
        from applications.forms import StudentInfoForm

        form = StudentInfoForm(tshirt_enabled=False)
        self.assertNotIn("tshirt_size", form.fields)

    def test_tshirt_field_present_when_enabled(self):
        from applications.forms import StudentInfoForm

        form = StudentInfoForm(tshirt_enabled=True)
        self.assertIn("tshirt_size", form.fields)


class RenumberingTests(TestCase):
    def setUp(self):
        School.objects.get_or_create(name="Pittsburgh High")

    def test_step5_post_advances_to_step6(self):
        app = _verified()
        date_of_birth_year_string = str(datetime.date.today().year - 12)
        response = self.client.post(
            reverse("apply_step5", kwargs={"app_id": app.application_id}),
            {
                "legal_first_name": "Grace",
                "last_name": "Hopper",
                "personal_email": "grace@example.com",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "tshirt_size": "M",
                "date_of_birth": date_of_birth_year_string + "-01-01",
                "school_name": "Pittsburgh High",
                "grade": "9",
            },
        )
        self.assertRedirects(
            response,
            reverse("apply_step6", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    def test_step_urls_and_views(self):
        app = _verified()
        response = self.client.get(
            reverse("apply_step7", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Primary adult contact")

        response = self.client.get(
            reverse("apply_step8", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Secondary adult contact")

        response = self.client.get(
            reverse("apply_step9", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Review and submit")


class Step8RepopulationTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(
            name="Spring 2030",
            start_date=timezone.localdate() + datetime.timedelta(days=60),
            end_date=timezone.localdate() + datetime.timedelta(days=120),
            active=True,
        )

    def test_step8_repopulates_when_navigating_back(self):
        app = _verified(program=self.program, current_step=8)
        app.data = {"step5-student": {"address": "123 Main St"}}
        app.save()

        self.client.post(
            reverse("apply_step8", kwargs={"app_id": app.application_id}),
            {
                "legal_first_name": "Secondary",
                "last_name": "Parent",
                "relationship_to_student": "guardian",
                "email": "secondary@example.com",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "phone_number": "123-456-7890",
                "phone_type": "cell",
            },
        )

        app.refresh_from_db()
        self.assertEqual(app.current_step, 9)
        self.assertIn("step8-secondaryparent", app.data)

        response = self.client.get(
            reverse("apply_step8", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="Secondary"')


class SwapParentsViewTests(TestCase):
    """Tests for the swap-parents endpoint."""

    def _app_with_both_parents(self, **kwargs):
        defaults = dict(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            current_step=8,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
            data={
                "step7-primaryparent": {
                    "legal_first_name": "Joe",
                    "last_name": "Primary",
                    "email": "joe@example.com",
                    "email_updates": True,
                },
                "step8-secondaryparent": {
                    "legal_first_name": "Jane",
                    "last_name": "Secondary",
                },
            },
        )
        defaults.update(kwargs)
        return Application.objects.create(**defaults)

    def test_swap_exchanges_step7_and_step8_data(self):
        app = self._app_with_both_parents()
        self.client.post(
            reverse("apply_swap_parents", kwargs={"app_id": app.application_id}),
            {"next": "7"},
        )
        app.refresh_from_db()
        self.assertEqual(app.data["step7-primaryparent"]["legal_first_name"], "Jane")
        self.assertEqual(app.data["step8-secondaryparent"]["legal_first_name"], "Joe")

    def test_swap_redirects_to_step7_by_default(self):
        app = self._app_with_both_parents()
        response = self.client.post(
            reverse("apply_swap_parents", kwargs={"app_id": app.application_id}),
            {"next": "7"},
        )
        self.assertRedirects(
            response,
            reverse("apply_step7", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    def test_swap_redirects_to_step8_when_requested(self):
        app = self._app_with_both_parents()
        response = self.client.post(
            reverse("apply_swap_parents", kwargs={"app_id": app.application_id}),
            {"next": "8"},
        )
        self.assertRedirects(
            response,
            reverse("apply_step8", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    def test_swap_twice_restores_original_data(self):
        app = self._app_with_both_parents()
        url = reverse("apply_swap_parents", kwargs={"app_id": app.application_id})
        self.client.post(url, {"next": "7"})
        self.client.post(url, {"next": "7"})
        app.refresh_from_db()
        self.assertEqual(app.data["step7-primaryparent"]["legal_first_name"], "Joe")
        self.assertEqual(app.data["step8-secondaryparent"]["legal_first_name"], "Jane")

    def test_swap_hydrates_from_student_record_when_steps_not_yet_saved(self):
        primary = Adult.objects.create(
            legal_first_name="Joe",
            last_name="Primary",
            personal_email="joe@example.com",
        )
        secondary = Adult.objects.create(
            legal_first_name="Jane",
            last_name="Secondary",
            personal_email="jane@example.com",
        )
        student = Student.objects.create(
            legal_first_name="Ada",
            last_name="Lovelace",
            primary_contact=primary,
            secondary_contact=secondary,
        )
        app = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="joe@example.com",
            current_step=7,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
            data={"step5-student": {"_existing_student_id": student.pk}},
        )
        self.client.post(
            reverse("apply_swap_parents", kwargs={"app_id": app.application_id}),
            {"next": "7"},
        )
        app.refresh_from_db()
        self.assertEqual(app.data["step7-primaryparent"]["legal_first_name"], "Jane")
        self.assertEqual(app.data["step8-secondaryparent"]["legal_first_name"], "Joe")

    def test_swap_requires_verified_email(self):
        app = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            current_step=7,
            data={
                "step7-primaryparent": {"legal_first_name": "Joe"},
                "step8-secondaryparent": {"legal_first_name": "Jane"},
            },
        )
        response = self.client.post(
            reverse("apply_swap_parents", kwargs={"app_id": app.application_id}),
            {"next": "7"},
        )
        self.assertRedirects(
            response,
            reverse("apply_step3", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )
        app.refresh_from_db()
        self.assertEqual(app.data["step7-primaryparent"]["legal_first_name"], "Joe")


class Step7SwapBoxVisibilityTests(TestCase):
    """Swap box on step 7 should appear when a secondary contact exists."""

    def setUp(self):
        today = timezone.localdate()
        self.program = Program.objects.create(
            name="Spring 2030",
            start_date=today + datetime.timedelta(days=60),
            end_date=today + datetime.timedelta(days=120),
            active=True,
        )
        self.primary = Adult.objects.create(
            legal_first_name="Joe",
            last_name="Primary",
            personal_email="joe@example.com",
        )
        self.secondary = Adult.objects.create(
            legal_first_name="Jane",
            last_name="Secondary",
            personal_email="jane@example.com",
        )
        self.student = Student.objects.create(
            legal_first_name="Ada",
            last_name="Lovelace",
            personal_email="ada@example.com",
            primary_contact=self.primary,
            secondary_contact=self.secondary,
        )

    def _app_with_existing_student(self):
        return Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="joe@example.com",
            current_step=7,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
            data={
                "step5-student": {"_existing_student_id": self.student.pk},
            },
        )

    def test_swap_box_shown_when_secondary_contact_exists_but_step8_not_saved(self):
        app = self._app_with_existing_student()
        response = self.client.get(
            reverse("apply_step7", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Swap primary")
        self.assertContains(response, "Jane")
        self.assertContains(response, "Secondary")

    def test_swap_box_hidden_when_no_secondary_contact_and_no_step8(self):
        self.student.secondary_contact = None
        self.student.save()
        app = self._app_with_existing_student()
        response = self.client.get(
            reverse("apply_step7", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Swap primary")

    def test_swap_box_shown_when_step7_already_saved_and_adult_has_secondary(self):
        app = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="joe@example.com",
            current_step=8,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
            data={
                "step5-student": {"_existing_student_id": self.student.pk},
                "step7-primaryparent": {
                    "legal_first_name": "Joe",
                    "last_name": "Primary",
                    "email": "joe@example.com",
                },
            },
        )
        response = self.client.get(
            reverse("apply_step7", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Swap primary")


class ResumeRedirectsToCurrentStepTests(TestCase):
    """Resume should land users on the right step 5/6/7/8/9."""

    def test_resume_to_step5(self):
        app = _verified(current_step=5)
        response = self.client.post(
            reverse("apply_resume"),
            {"application_id": app.application_id},
        )
        self.assertRedirects(
            response,
            reverse("apply_step5", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    def test_resume_to_step6(self):
        app = _verified(current_step=6)
        response = self.client.post(
            reverse("apply_resume"),
            {"application_id": app.application_id},
        )
        self.assertRedirects(
            response,
            reverse("apply_step6", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )

    def test_resume_to_step9(self):
        app = _verified(current_step=9)
        response = self.client.post(
            reverse("apply_resume"),
            {"application_id": app.application_id},
        )
        self.assertRedirects(
            response,
            reverse("apply_step9", kwargs={"app_id": app.application_id}),
            fetch_redirect_response=False,
        )


class Step7AddressCopyTests(TestCase):
    def test_step7_has_student_address_in_context(self):
        app = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            current_step=7,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
            data={
                "step5-student": {
                    "address": "123 Main St",
                    "city": "Pittsburgh",
                    "state": "PA",
                    "zip_code": "15213",
                }
            },
        )
        url = reverse("apply_step7", kwargs={"app_id": app.application_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context.get("student_address"), "123 Main St")
        self.assertEqual(response.context.get("student_city"), "Pittsburgh")
        self.assertEqual(response.context.get("student_state"), "PA")
        self.assertEqual(response.context.get("student_zip_code"), "15213")

    def test_step8_has_student_address_in_context(self):
        app = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            current_step=8,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
            data={
                "step5-student": {
                    "address": "123 Main St",
                    "city": "Pittsburgh",
                    "state": "PA",
                    "zip_code": "15213",
                }
            },
        )
        url = reverse("apply_step8", kwargs={"app_id": app.application_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context.get("student_address"), "123 Main St")

    def test_step7_fallback_to_student_model(self):
        student = Student.objects.create(
            legal_first_name="Jane",
            last_name="Doe",
            personal_email="jane@example.com",
            address="456 Elm St",
            city="Pittsburgh",
            state="PA",
            zip_code="15201",
        )
        app = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            current_step=7,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
            data={"step5-student": {"_existing_student_id": student.pk}},
        )
        url = reverse("apply_step7", kwargs={"app_id": app.application_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context.get("student_address"), "456 Elm St")
        self.assertEqual(response.context.get("student_city"), "Pittsburgh")

    def test_step7_fallback_to_old_key(self):
        app = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            current_step=7,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
            data={
                "step5": {
                    "address": "789 Pine St",
                    "city": "Cleveland",
                    "state": "OH",
                    "zip_code": "44101",
                }
            },
        )
        url = reverse("apply_step7", kwargs={"app_id": app.application_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context.get("student_address"), "789 Pine St")
        self.assertEqual(response.context.get("student_city"), "Cleveland")

    def test_button_not_rendered_when_address_missing(self):
        app = Application.objects.create(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            current_step=7,
            email_verified_at=timezone.now(),
            status=Application.Status.EMAIL_VERIFIED,
            data={},
        )
        url = reverse("apply_step7", kwargs={"app_id": app.application_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Copy address from student")
        self.assertNotContains(response, "function copyStudentAddress")
