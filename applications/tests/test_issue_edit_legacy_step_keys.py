"""Regression tests: legacy applications with duplicate step keys.

Before the wizard step-data keys were renamed (``step5`` → ``step5-student``,
``step6`` → ``step6-experience``, ``step7`` → ``step7-primaryparent``,
``step8`` → ``step8-secondaryparent``, ``step6_handoff`` → ``step7_handoff``)
older applications in the database hold their data under the legacy keys.

The lead-mentor "Edit captured data" page only knows the current key names, so
editing a legacy application used to re-save the submitted fields under the new
keys while leaving the legacy keys in place — producing *two* step-5 results
and an application that would not convert correctly (conversion/display only
read the current keys, which for a legacy app were either missing or junk).

These tests pin the normalization that walks legacy keys into their current
names so review, edit, and conversion all work on old applications.
"""

from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from applications.services import convert_application_to_student
from programs.models import Program

User = get_user_model()

LEAD_MENTORS_GROUP = "LeadMentor"
REVIEW_PERM_CODENAME = "review_application"

# A complete legacy step-5 block (the richer of the two dicts in real bug data).
LEGACY_STUDENT = {
    "legal_first_name": "Jane",
    "last_name": "Smith",
    "date_of_birth": "2010-01-01",
    "personal_email": "jane@example.com",
    "address": "123 Main St",
    "_existing_student_id": 7,
}

# Junk re-saved over the current key by the pre-fix edit page (a mostly-empty
# dict with no date of birth — has fewer filled values than the legacy one).
JUNK_STUDENT = {
    "legal_first_name": "Snarf",
    "last_name": "Snarf",
    "date_of_birth": None,
    "directory_consent": True,
    "can_receive_texts": True,
}

# Legacy parent steps used "first_name" for the legal first name.
LEGACY_PRIMARY = {
    "first_name": "Pat",
    "last_name": "Parent",
    "email": "pat@example.com",
    "relationship_to_student": "mother",
}

LEGACY_SECONDARY = {
    "first_name": "Sam",
    "last_name": "Parent",
    "email": "sam@example.com",
}


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


def _make_application(data, **overrides):
    defaults = dict(
        applicant_type=Application.Type.PARENT,
        email="jane@example.com",
        current_step=8,
        email_verified_at=timezone.now(),
        status=Application.Status.SUBMITTED,
        submitted_at=timezone.now(),
        data=data,
    )
    defaults.update(overrides)
    return Application.objects.create(**defaults)


class LegacyStepKeyRegressionTests(TestCase):
    def setUp(self):
        self.client.force_login(_reviewer_user())

    def test_edit_get_prefills_and_post_migrates_legacy_keys(self):
        app = _make_application(
            {
                "step5": dict(LEGACY_STUDENT),
                "step6": {"interest_reason": "Robots!"},
                "step7": dict(LEGACY_PRIMARY),
                "step6_handoff": {"parent_email": "pat@example.com"},
            }
        )
        url = reverse("application_review_edit", kwargs={"app_id": app.application_id})

        # GET prefills from the legacy keys (student legal name + parent name).
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="Jane"')
        self.assertContains(response, 'value="Pat"')

        # Resubmitting the form (the lead mentor just fixing a typo) must
        # write to the current keys and drop the legacy duplicates.
        response = self.client.post(
            url,
            {
                "step5-student__legal_first_name": "Jane",
                "step5-student__last_name": "Smith",
                "step5-student__date_of_birth": "2010-01-01",
                "step6-experience__interest_reason": "Robots!",
                "step7-primaryparent__legal_first_name": "Pat",
                "step7-primaryparent__last_name": "Parent",
                "step7_handoff__parent_email": "pat@example.com",
            },
        )
        self.assertEqual(response.status_code, 302)
        app.refresh_from_db()
        for legacy in ("step5", "step6", "step7", "step6_handoff"):
            self.assertNotIn(legacy, app.data)
        self.assertEqual(app.data["step5-student"]["legal_first_name"], "Jane")
        self.assertEqual(app.data["step5-student"]["_existing_student_id"], 7)
        self.assertEqual(app.data["step6-experience"]["interest_reason"], "Robots!")
        self.assertEqual(app.data["step7-primaryparent"]["legal_first_name"], "Pat")
        self.assertEqual(app.data["step7_handoff"]["parent_email"], "pat@example.com")

    def test_review_detail_merges_duplicate_step5(self):
        app = _make_application(
            {
                "step5": dict(LEGACY_STUDENT),
                "step5-student": dict(JUNK_STUDENT),
            }
        )
        url = reverse(
            "application_review_detail", kwargs={"app_id": app.application_id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Only ONE step-5 section rendered, using the complete legacy data.
        self.assertContains(response, "Smith")
        self.assertNotContains(response, "Snarf")
        # The merge is persisted, so it survives a reload.
        app.refresh_from_db()
        self.assertNotIn("step5", app.data)
        self.assertEqual(app.data["step5-student"]["legal_first_name"], "Jane")
        self.assertEqual(app.data["step5-student"]["date_of_birth"], "2010-01-01")

    def test_conversion_from_legacy_keys_only(self):
        program = Program.objects.create(name="Test Program")
        app = _make_application(
            {
                "step5": dict(LEGACY_STUDENT),
                "step6": {"interest_reason": "Robots!"},
                "step7": dict(LEGACY_PRIMARY),
                "step8": dict(LEGACY_SECONDARY),
                "step6_handoff": {"parent_email": "pat@example.com"},
            },
            program=program,
            status=Application.Status.APPROVED_SIGNED,
        )
        student = convert_application_to_student(app)
        student.refresh_from_db()
        self.assertEqual(student.legal_first_name, "Jane")
        self.assertEqual(student.last_name, "Smith")
        self.assertEqual(student.date_of_birth, date(2010, 1, 1))
        self.assertEqual(student.primary_contact.personal_email, "pat@example.com")
        self.assertEqual(student.secondary_contact.personal_email, "sam@example.com")
        app.refresh_from_db()
        self.assertNotIn("step5", app.data)
        self.assertEqual(app.data["step5-student"]["legal_first_name"], "Jane")

    def test_conversion_prefers_complete_legacy_step5(self):
        program = Program.objects.create(name="Test Program")
        app = _make_application(
            {
                "step5": dict(LEGACY_STUDENT),
                "step5-student": dict(JUNK_STUDENT),
                "step7": dict(LEGACY_PRIMARY),
            },
            program=program,
            status=Application.Status.APPROVED_SIGNED,
        )
        student = convert_application_to_student(app)
        # The junk current-key copy (no date of birth) must not win.
        student.refresh_from_db()
        self.assertEqual(student.legal_first_name, "Jane")
        self.assertEqual(student.last_name, "Smith")
        self.assertEqual(student.date_of_birth, date(2010, 1, 1))
        self.assertEqual(student.primary_contact.personal_email, "pat@example.com")
        app.refresh_from_db()
        self.assertNotIn("step5", app.data)
        self.assertEqual(app.data["step5-student"]["legal_first_name"], "Jane")

    def test_saving_edit_without_parent_data_does_not_create_empty_section(self):
        # The edit form's default-checked checkboxes ("can receive texts?") used
        # to count as a "touched" parent step, so a bare submit created an
        # empty step7-primaryparent block full of default values.
        app = _make_application(
            {"step5-student": {"legal_first_name": "Grace", "last_name": "Hopper"}}
        )
        url = reverse("application_review_edit", kwargs={"app_id": app.application_id})
        response = self.client.post(
            url,
            {
                "step5-student__legal_first_name": "Grace",
                "step5-student__last_name": "Hopper",
                "step7-primaryparent__can_receive_texts": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        app.refresh_from_db()
        self.assertNotIn("step7-primaryparent", app.data)
        self.assertEqual(app.data["step5-student"]["legal_first_name"], "Grace")
