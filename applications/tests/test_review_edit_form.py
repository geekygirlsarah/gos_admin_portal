"""Tests for the per-field application review edit form.

The lead-mentor "Edit captured data" screen used to be a raw JSON textarea.
It is now a per-field form that renders every wizard step's fields in
application order and saves them back into the nested ``Application.data``
JSON while preserving internal keys (e.g. ``_existing_student_id``).
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from applications.models import Application

User = get_user_model()

LEAD_MENTORS_GROUP = "LeadMentor"
REVIEW_PERM_CODENAME = "review_application"


def _make_application(**overrides):
    defaults = dict(
        applicant_type=Application.Type.PARENT,
        email="parent@example.com",
        current_step=8,
        email_verified_at=timezone.now(),
        status=Application.Status.SUBMITTED,
        submitted_at=timezone.now(),
        data={
            "step5-student": {
                "legal_first_name": "Ada",
                "last_name": "Lovelace",
                "_existing_student_id": 7,
            },
            "step7-primaryparent": {
                "legal_first_name": "Pat",
                "last_name": "Parent",
                "email": "parent@example.com",
            },
        },
    )
    defaults.update(overrides)
    return Application.objects.create(**defaults)


def _reviewer_user(username="lead"):
    ct, _ = ContentType.objects.get_or_create(
        app_label="applications", model="application"
    )
    perm, _ = Permission.objects.get_or_create(
        content_type=ct,
        codename=REVIEW_PERM_CODENAME,
        defaults={"name": "Can review applications"},
    )
    group, _ = Group.objects.get_or_create(name=LEAD_MENTORS_GROUP)
    group.permissions.add(perm)
    user = User.objects.create_user(username=username, email=f"{username}@x.test")
    user.groups.add(group)
    return user


class ReviewEditFormTests(TestCase):
    def setUp(self):
        self.app = _make_application()
        self.client.force_login(_reviewer_user())
        self.url = reverse(
            "application_review_edit", kwargs={"app_id": self.app.application_id}
        )

    def test_get_renders_student_sections_in_order(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        student = body.index("Student information")
        experience = body.index("Student experience")
        primary = body.index("Primary parent / guardian")
        secondary = body.index("Secondary parent / guardian")
        self.assertLess(student, experience)
        self.assertLess(experience, primary)
        self.assertLess(primary, secondary)
        # Student sections only — no mentor sections.
        self.assertNotIn("Mentor information", body)

    def test_get_renders_mentor_sections(self):
        mentor = _make_application(
            applicant_type=Application.Type.MENTOR,
            data={"mentor_info": {"last_name": "Ride"}},
        )
        url = reverse(
            "application_review_edit", kwargs={"app_id": mentor.application_id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mentor information")
        self.assertContains(response, "Clearance details")
        # No student/parent sections.
        self.assertNotIn("Student information", response.content.decode())

    def test_post_saves_fields_and_preserves_internal_keys(self):
        response = self.client.post(
            self.url,
            {
                "step5-student__legal_first_name": "Grace",
                "step5-student__last_name": "Hopper",
                "step7-primaryparent__legal_first_name": "Pam",
                "step7-primaryparent__email": "pam@example.com",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.app.refresh_from_db()
        step5 = self.app.data["step5-student"]
        self.assertEqual(step5["legal_first_name"], "Grace")
        self.assertEqual(step5["last_name"], "Hopper")
        # Internal keys that aren't in the form are preserved.
        self.assertEqual(step5["_existing_student_id"], 7)
        self.assertEqual(
            self.app.data["step7-primaryparent"]["legal_first_name"], "Pam"
        )

    def test_post_updates_application_email(self):
        response = self.client.post(self.url, {"email": "new-parent@example.com"})
        self.assertEqual(response.status_code, 302)
        self.app.refresh_from_db()
        self.assertEqual(self.app.email, "new-parent@example.com")

    def test_blank_sections_are_not_created(self):
        # A completely untouched section should not be added to data.
        self.app.data = {"step5-student": {"last_name": "Lovelace"}}
        self.app.save()
        response = self.client.post(self.url, {"step5-student__last_name": "Lovelace"})
        self.assertEqual(response.status_code, 302)
        self.app.refresh_from_db()
        self.assertNotIn("step6-experience", self.app.data)
        self.assertNotIn("step8-secondaryparent", self.app.data)

    def test_post_rejects_invalid_date(self):
        response = self.client.post(
            self.url, {"step5-student__date_of_birth": "not-a-date"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a valid date")
