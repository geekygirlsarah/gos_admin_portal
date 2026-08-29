"""Digital sign-out tests: config model, public page, signature recording."""

import base64

from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.models import (
    DigitalSignout,
    DigitalSignoutConfig,
    StudentPresence,
)
from attendance.signout_views import _decode_signature
from programs.models import AdultStudentRelationship, Enrollment

from .base import (
    make_adult,
    make_lead_mentor_user,
    make_mentor_user,
    make_parent_user,
    make_program,
    make_student,
)

# A tiny valid 1x1 PNG (bytes) for signature upload tests.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
TINY_PNG_DATA_URL = "data:image/png;base64," + base64.b64encode(TINY_PNG).decode()


def _enroll(student, program, active=True):
    return Enrollment.objects.create(student=student, program=program, active=active)


def _unlock(client, config_id):
    client.cookies[f"signout_unlocked_{config_id}"] = "1"


class DigitalSignoutParentPickerTests(TestCase):
    """Parent/guardian dropdown: populated per student + records chosen name."""

    def setUp(self):
        self.client = Client()
        self.program = make_program()
        self.config = DigitalSignoutConfig.objects.create(
            label="Front Door", program=self.program
        )
        self.student = make_student(preferred_first_name="Ada", last_name="Lovelace")
        Enrollment.objects.create(student=self.student, program=self.program)
        self.parent = make_adult(
            legal_first_name="Jane", last_name="Parent", is_parent=True
        )
        AdultStudentRelationship.objects.create(adult=self.parent, student=self.student)
        _unlock(self.client, self.config.pk)
        self.url = reverse("digital_signout", args=[self.config.pk])

    def test_dropdown_populated_with_linked_parent(self):
        response = self.client.get(self.url)
        # json_script context for the picker should include the parent's name
        # for this student.
        self.assertContains(response, "Jane Parent")

    def test_post_records_chosen_parent_as_signed_by_name(self):
        response = self.client.post(
            self.url,
            {
                "student_id": self.student.pk,
                "signed_by_name": "Jane Parent",
                "signature": TINY_PNG_DATA_URL,
            },
        )
        self.assertEqual(response.status_code, 302)
        signout = DigitalSignout.objects.get()
        self.assertEqual(signout.signed_by_name, "Jane Parent")


class AttendancePresenceTests(TestCase):
    """StudentPresence model + Who's Here Today mentor/tablet behavior."""

    def setUp(self):
        self.client = Client()
        self.program = make_program()
        self.config = DigitalSignoutConfig.objects.create(
            label="Front Door", program=self.program
        )
        self.student = make_student(preferred_first_name="Ada", last_name="Lovelace")
        Enrollment.objects.create(student=self.student, program=self.program)
        self.url = reverse("digital_signout", args=[self.config.pk])
        self.mgmt_url = reverse("program_digital_signout", args=[self.program.pk])
        _unlock(self.client, self.config.pk)

    # ── Model defaults / uniqueness ───────────────────────────────────────

    def test_presence_defaults_to_today(self):
        p = StudentPresence.objects.create(
            program=self.program, student=self.student, status=StudentPresence.PRESENT
        )
        self.assertEqual(p.date, timezone.localdate())

    def test_per_day_record_overwrites_status(self):
        # The model enforces one record per program/student/date (unique
        # together); "overwrite" is implemented in the view via update_or_create
        # so toggling marks today's row instead of creating duplicates.
        StudentPresence.objects.create(
            program=self.program, student=self.student, status=StudentPresence.PRESENT
        )
        StudentPresence.objects.update_or_create(
            program=self.program,
            student=self.student,
            date=timezone.localdate(),
            defaults={"status": StudentPresence.ABSENT},
        )
        self.assertEqual(StudentPresence.objects.count(), 1)
        self.assertEqual(StudentPresence.objects.get().status, StudentPresence.ABSENT)

    # ── Mentor management UI (Who's Here Today) ──────────────────────────

    def test_mentor_can_mark_absent(self):
        client = Client()
        client.force_login(make_lead_mentor_user())
        response = client.post(
            self.mgmt_url,
            {"action": "mark_absent", "student_id": self.student.pk},
        )
        self.assertEqual(response.status_code, 302)
        p = StudentPresence.objects.get(student=self.student)
        self.assertEqual(p.status, StudentPresence.ABSENT)
        self.assertIsNotNone(p.marked_by)

    def test_mentor_can_mark_present(self):
        StudentPresence.objects.create(
            program=self.program, student=self.student, status=StudentPresence.ABSENT
        )
        client = Client()
        client.force_login(make_lead_mentor_user())
        response = client.post(
            self.mgmt_url,
            {"action": "mark_present", "student_id": self.student.pk},
        )
        self.assertEqual(response.status_code, 302)
        p = StudentPresence.objects.get(student=self.student)
        self.assertEqual(p.status, StudentPresence.PRESENT)

    def test_non_mentor_cannot_mark_presence(self):
        client = Client()
        client.force_login(make_parent_user())
        client.post(
            self.mgmt_url,
            {"action": "mark_absent", "student_id": self.student.pk},
        )
        # Parent is not authorized for program_digital_signout challenge/view
        self.assertEqual(StudentPresence.objects.count(), 0)

    def test_management_view_shows_today_status(self):
        StudentPresence.objects.create(
            program=self.program, student=self.student, status=StudentPresence.ABSENT
        )
        client = Client()
        client.force_login(make_lead_mentor_user())
        response = client.get(self.mgmt_url)
        self.assertContains(response, "Who&#x27;s Here Today")
        self.assertContains(response, "Ada")

    # ── Tablet view: absent hidden, present/unmarked shown ───────────────

    def test_absent_student_hidden_from_picker(self):
        StudentPresence.objects.create(
            program=self.program, student=self.student, status=StudentPresence.ABSENT
        )
        response = self.client.get(self.url)
        self.assertNotContains(response, "Ada")

    def test_unmarked_student_renders_normally(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Ada")

    def test_present_student_renders_and_signs_out(self):
        StudentPresence.objects.create(
            program=self.program, student=self.student, status=StudentPresence.PRESENT
        )
        response = self.client.get(self.url)
        self.assertContains(response, "Ada")
        resp = self.client.post(
            self.url,
            {
                "student_id": self.student.pk,
                "signed_by_name": "Jane Parent",
                "signature": TINY_PNG_DATA_URL,
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(DigitalSignout.objects.count(), 1)

    def test_signing_out_absent_student_rejected(self):
        StudentPresence.objects.create(
            program=self.program, student=self.student, status=StudentPresence.ABSENT
        )
        resp = self.client.post(
            self.url,
            {
                "student_id": self.student.pk,
                "signed_by_name": "Jane Parent",
                "signature": TINY_PNG_DATA_URL,
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(DigitalSignout.objects.count(), 0)


class SignoutConfigModelTests(TestCase):
    def setUp(self):
        self.program = make_program()

    def test_config_str(self):
        config = DigitalSignoutConfig.objects.create(
            label="Front Door", program=self.program
        )
        self.assertIn("Front Door", str(config))

    def test_config_defaults_to_active(self):
        config = DigitalSignoutConfig.objects.create(
            label="Front Door", program=self.program
        )
        self.assertTrue(config.is_active)


class DigitalSignoutPageTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.program = make_program()
        self.config = DigitalSignoutConfig.objects.create(
            label="Front Door", program=self.program
        )
        self.student = make_student(preferred_first_name="Ada", last_name="Lovelace")
        Enrollment.objects.create(student=self.student, program=self.program)
        _unlock(self.client, self.config.pk)

    def test_page_is_public_no_redirect(self):
        url = reverse("digital_signout", args=[self.config.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_page_title_and_program(self):
        url = reverse("digital_signout", args=[self.config.pk])
        response = self.client.get(url)
        self.assertContains(response, self.program.name)
        self.assertContains(response, self.config.label)

    def test_locked_page_hides_form(self):
        locked_client = Client()
        url = reverse("digital_signout", args=[self.config.pk])
        response = locked_client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "locked")
        self.assertNotContains(response, "studentPicker")

    def test_page_lists_active_enrolled_students(self):
        url = reverse("digital_signout", args=[self.config.pk])
        response = self.client.get(url)
        self.assertContains(response, "Ada")
        self.assertContains(response, "Lovelace")

    def test_graduated_student_is_not_listed(self):
        self.student.graduated = True
        self.student.save()
        url = reverse("digital_signout", args=[self.config.pk])
        response = self.client.get(url)
        self.assertNotContains(response, "Ada")

    def test_inactive_config_not_accessible(self):
        self.config.is_active = False
        self.config.save()
        url = reverse("digital_signout", args=[self.config.pk])
        response = self.client.get(url)
        self.assertIn(response.status_code, [302, 404])


class DigitalSignoutSubmissionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.program = make_program()
        self.config = DigitalSignoutConfig.objects.create(
            label="Front Door", program=self.program
        )
        self.student = make_student(preferred_first_name="Ada", last_name="Lovelace")
        Enrollment.objects.create(student=self.student, program=self.program)
        self.url = reverse("digital_signout", args=[self.config.pk])
        _unlock(self.client, self.config.pk)

    def _post(self, **overrides):
        data = {
            "student_id": self.student.pk,
            "signed_by_name": "Jane Parent",
            "signature": TINY_PNG_DATA_URL,
        }
        data.update(overrides)
        return self.client.post(self.url, data)

    def test_valid_submission_redirects_to_confirmation(self):
        """A successful POST redirects (PRG) so reloading never re-submits."""
        response = self._post()
        self.assertEqual(response.status_code, 302)
        station_url = reverse("digital_signout", args=[self.config.pk])
        # `_confirmation_url` builds an absolute Location; verify the path
        # matches the station URL and carries the signout id.
        self.assertIn(station_url, response.url)
        signout = DigitalSignout.objects.get()
        self.assertIn(f"done={signout.pk}", response.url)
        self.assertEqual(signout.student, self.student)
        self.assertEqual(signout.signed_by_name, "Jane Parent")
        self.assertEqual(signout.program, self.program)
        self.assertTrue(signout.signature)
        self.assertTrue(signout.signature.name.endswith(".png"))

    def test_confirmation_page_shows_signed_out(self):
        response = self._post()
        self.assertEqual(response.status_code, 302)
        followed = response.client.get(response.url)
        self.assertEqual(followed.status_code, 200)
        self.assertContains(followed, "Signed Out")
        self.assertEqual(DigitalSignout.objects.count(), 1)

    def test_done_returns_to_fresh_form_for_next_signout(self):
        """The "Done" flow (GET without query) shows the form so another
        student can be signed out — no double-record on refresh."""
        self._post()  # sign out Ada
        self.assertEqual(DigitalSignout.objects.count(), 1)
        # "Done" navigates to the bare URL (no ?done=) -> a fresh form
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "studentPicker")
        self.assertNotContains(response, "Signed Out")
        # signing out a *different* student is allowed and creates a second record
        other = make_student(preferred_first_name="Grace", last_name="Hopper")
        Enrollment.objects.create(student=other, program=self.program)
        response = self.client.post(
            self.url,
            {
                "student_id": other.pk,
                "signed_by_name": "Jane Parent",
                "signature": TINY_PNG_DATA_URL,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DigitalSignout.objects.count(), 2)

    def test_signature_file_contents_are_png(self):
        self._post()
        signout = DigitalSignout.objects.get()
        with signout.signature.open("rb") as f:
            content = f.read()
        self.assertTrue(content.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_missing_signed_by_name_rejected(self):
        response = self._post(signed_by_name="")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DigitalSignout.objects.count(), 0)

    def test_empty_signature_rejected(self):
        response = self._post(signature="")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DigitalSignout.objects.count(), 0)

    def test_invalid_png_signature_rejected(self):
        bad_data_url = "data:image/png;base64," + base64.b64encode(b"not png").decode()
        response = self._post(signature=bad_data_url)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DigitalSignout.objects.count(), 0)

    def test_student_from_another_program_rejected(self):
        other_program = make_program(name="Other")
        other_student = make_student(preferred_first_name="Grace", last_name="Hopper")
        Enrollment.objects.create(student=other_student, program=other_program)
        response = self._post(student_id=other_student.pk)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DigitalSignout.objects.count(), 0)


class DigitalSignoutManagementTests(TestCase):
    """Program-page management view: access control, unlock/lock cookie, config."""

    def setUp(self):
        self.program = make_program()
        self.config = DigitalSignoutConfig.objects.create(
            label="Front Door", program=self.program
        )
        self.url = reverse("program_digital_signout", args=[self.program.pk])

    def test_unauthenticated_redirects(self):
        response = Client().get(self.url)
        self.assertIn(response.status_code, [301, 302])

    def test_lead_mentor_can_access(self):
        client = Client()
        client.force_login(make_lead_mentor_user())
        response = client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_mentor_can_access(self):
        client = Client()
        client.force_login(make_mentor_user())
        response = client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_parent_denied(self):
        from django.contrib.auth.models import User

        from programs.models import Adult

        client = Client()
        user = User.objects.create_user("parent", password="password123")  # nosec B106
        Adult.objects.create(
            user=user, legal_first_name="Parent", last_name="User", is_parent=True
        )
        client.force_login(user)
        response = client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_management_page_ensures_config_for_program(self):
        # Delete the pre-created config so the view must create one.
        self.config.delete()
        self.assertEqual(DigitalSignoutConfig.objects.count(), 0)
        client = Client()
        client.force_login(make_lead_mentor_user())
        response = client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(DigitalSignoutConfig.objects.count(), 1)

    def test_unlock_sets_cookie(self):
        client = Client()
        client.force_login(make_lead_mentor_user())
        response = client.post(self.url, {"action": "unlock"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            client.cookies[f"signout_unlocked_{self.config.pk}"].value, "1"
        )

    def test_lock_clears_cookie(self):
        client = Client()
        client.force_login(make_lead_mentor_user())
        client.post(self.url, {"action": "unlock"})
        client.post(self.url, {"action": "lock"})
        cookie = client.cookies.get(f"signout_unlocked_{self.config.pk}")
        # Django marks deleted cookies with a falsy / empty-ifier; treat as cleared
        self.assertTrue(cookie is None or not cookie.value)


class DigitalSignoutUndoTests(TestCase):
    """Dimming already-signed-out students + undoing a sign-out."""

    def setUp(self):
        self.client = Client()
        self.program = make_program()
        self.config = DigitalSignoutConfig.objects.create(
            label="Front Door", program=self.program
        )
        self.student = make_student(preferred_first_name="Ada", last_name="Lovelace")
        Enrollment.objects.create(student=self.student, program=self.program)
        self.url = reverse("digital_signout", args=[self.config.pk])
        _unlock(self.client, self.config.pk)
        self.signout = DigitalSignout.objects.create(
            config=self.config,
            program=self.program,
            student=self.student,
            signed_by_name="Jane Parent",
            signed_at=timezone.now(),
        )

    def _post(self, **overrides):
        data = {
            "student_id": self.student.pk,
            "signed_by_name": "Jane Parent",
            "signature": TINY_PNG_DATA_URL,
        }
        data.update(overrides)
        return self.client.post(self.url, data)

    def test_signed_out_student_is_marked_dimmer(self):
        response = self.client.get(self.url)
        self.assertContains(response, f'data-signed-out="{self.signout.pk}"')

    def test_unsigned_student_is_not_marked(self):
        other = make_student(preferred_first_name="Grace", last_name="Hopper")
        Enrollment.objects.create(student=other, program=self.program)
        response = self.client.get(self.url)
        self.assertContains(response, "Grace")
        # Only Ada (signed out) has the card marker — the unsigned Grace does not
        self.assertContains(response, 'data-signed-out="', count=1)

    def test_undo_deletes_signout(self):
        response = self.client.post(
            self.url, {"action": "undo", "signout_id": self.signout.pk}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DigitalSignout.objects.count(), 0)
        page = self.client.get(self.url)
        self.assertNotContains(page, 'data-signed-out="')

    def test_undo_does_not_delete_old_signout(self):
        old = DigitalSignout.objects.create(
            config=self.config,
            program=self.program,
            student=self.student,
            signed_by_name="Old",
            signed_at=timezone.now() - timezone.timedelta(days=2),
        )
        self.assertEqual(DigitalSignout.objects.count(), 2)
        response = self.client.post(self.url, {"action": "undo", "signout_id": old.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DigitalSignout.objects.count(), 2)

    def test_undo_does_not_delete_other_config_signout(self):
        other_program = make_program(name="Other")
        other_config = DigitalSignoutConfig.objects.create(
            label="Other", program=other_program
        )
        other = DigitalSignout.objects.create(
            config=other_config,
            program=other_program,
            student=self.student,
            signed_by_name="X",
            signed_at=timezone.now(),
        )
        self.assertEqual(DigitalSignout.objects.count(), 2)
        response = self.client.post(
            self.url, {"action": "undo", "signout_id": other.pk}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DigitalSignout.objects.count(), 2)

    def test_undo_requires_unlock(self):
        locked_client = Client()
        response = locked_client.post(
            self.url, {"action": "undo", "signout_id": self.signout.pk}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(DigitalSignout.objects.count(), 1)

    def test_second_signout_same_student_blocked(self):
        response = self._post()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DigitalSignout.objects.count(), 1)


class DecodeSignatureTests(TestCase):
    def test_valid_png_data_url_decodes(self):
        raw = _decode_signature(TINY_PNG_DATA_URL)
        self.assertEqual(raw, TINY_PNG)

    def test_non_png_url_rejected(self):
        with self.assertRaises(ValidationError):
            _decode_signature("data:image/jpeg;base64,AAAA")

    def test_empty_rejected(self):
        with self.assertRaises(ValidationError):
            _decode_signature("")

    def test_short_signature_rejected(self):
        data_url = "data:image/png;base64," + base64.b64encode(TINY_PNG[:10]).decode()
        with self.assertRaises(ValidationError):
            _decode_signature(data_url)
