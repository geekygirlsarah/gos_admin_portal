"""Tests for review page rendering: boolean fields and AdultStudentRelationship prefill."""

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from applications.tests.test_review_workflow import _reviewer_user
from programs.models import Adult, AdultStudentRelationship, Program, Student


class BooleanRenderingReproductionTest(TestCase):
    def setUp(self):
        self.reviewer = _reviewer_user()
        self.client.force_login(self.reviewer)

    def test_boolean_fields_displayed_as_yes_no(self):
        app = Application.objects.create(
            email="test@example.com",
            data={
                "step1": {
                    "some_checkbox": False,
                    "other_checkbox": True,
                },
            },
        )
        url = reverse(
            "application_review_detail", kwargs={"app_id": app.application_id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Some checkbox")
        self.assertContains(response, "No")
        self.assertContains(response, "Other checkbox")
        self.assertContains(response, "Yes")


class ReviewListNameAndStatusLabelTest(TestCase):
    """The review list shows the applicant's name instead of email, and
    converted mentor applications read "Converted to Mentor"."""

    def setUp(self):
        self.reviewer = _reviewer_user()
        self.client.force_login(self.reviewer)
        self.list_url = reverse("application_review_list")

    def _app(self, **overrides):
        defaults = dict(
            email="applicant@example.com",
            status=Application.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )
        defaults.update(overrides)
        return Application.objects.create(**defaults)

    def test_list_shows_student_name_not_email(self):
        self._app(
            applicant_type=Application.Type.STUDENT,
            data={
                "step5-student": {
                    "legal_first_name": "Ada",
                    "last_name": "Lovelace",
                }
            },
        )
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ada Lovelace")
        self.assertNotContains(response, "applicant@example.com")

    def test_list_shows_mentor_name_not_email(self):
        self._app(
            applicant_type=Application.Type.MENTOR,
            data={
                "mentor_info": {
                    "legal_first_name": "Grace",
                    "last_name": "Hopper",
                }
            },
        )
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Grace Hopper")
        self.assertNotContains(response, "applicant@example.com")

    def test_list_name_column_does_not_sort_by_email(self):
        self._app(
            applicant_type=Application.Type.STUDENT,
            data={
                "step5-student": {
                    "legal_first_name": "Ada",
                    "last_name": "Lovelace",
                }
            },
        )
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Student/Mentor Name")
        self.assertNotContains(response, "sort=email")

    def test_converted_mentor_status_label_on_list(self):
        self._app(
            applicant_type=Application.Type.MENTOR,
            status=Application.Status.CONVERTED,
            data={
                "mentor_info": {
                    "legal_first_name": "Grace",
                    "last_name": "Hopper",
                }
            },
        )
        response = self.client.get(self.list_url)
        self.assertContains(response, "Converted to Mentor")

    def test_converted_student_status_label_unchanged(self):
        self._app(
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.CONVERTED,
            data={
                "step5-student": {
                    "legal_first_name": "Ada",
                    "last_name": "Lovelace",
                }
            },
        )
        response = self.client.get(self.list_url)
        self.assertContains(response, "Converted to Student")

    def test_converted_mentor_status_label_on_detail(self):
        app = self._app(
            applicant_type=Application.Type.MENTOR,
            status=Application.Status.CONVERTED,
            data={
                "mentor_info": {
                    "legal_first_name": "Grace",
                    "last_name": "Hopper",
                }
            },
        )
        url = reverse(
            "application_review_detail", kwargs={"app_id": app.application_id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Converted to Mentor")
        self.assertNotContains(response, "Converted to Student")


class AdultToPrefillAttributeErrorTest(TestCase):
    """Test that adult_to_prefill and Step 8 view no longer crash when
    prefilling data for an existing student/adult relationship.
    """

    def setUp(self):
        self.program = Program.objects.create(name="Test Program", active=True)
        self.adult = Adult.objects.create(
            first_name="Volida",
            last_name="Abdurazakova",
            personal_email="volida@example.com",
        )
        self.student = Student.objects.create(
            first_name="Zebo",
            last_name="Sarkarov",
            secondary_contact=self.adult,
        )
        self.asr = AdultStudentRelationship.objects.create(
            adult=self.adult,
            student=self.student,
            relationship_to_student="parent",
        )
        self.application = Application.objects.create(
            program=self.program,
            email="zebo.sarkarov12@gmail.com",
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.EMAIL_VERIFIED,
            email_verified_at=timezone.now(),
            data={"step5-student": {"_existing_student_id": self.student.pk}},
        )

    def test_step8_prefill_no_longer_triggers_attribute_error(self):
        from applications.services import adult_to_prefill

        data = adult_to_prefill(self.adult, student=self.student)
        self.assertEqual(data["relationship_to_student"], "parent")
        self.assertEqual(data["first_name"], "Volida")

    def test_view_no_longer_triggers_attribute_error(self):
        url = reverse("apply_step8", kwargs={"app_id": self.application.application_id})
        session = self.client.session
        session[f"handoff_{self.application.application_id}"] = True
        session.save()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Volida")
