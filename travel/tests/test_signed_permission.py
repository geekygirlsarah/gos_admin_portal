from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from uuid import UUID

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

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
from travel.models import (
    TravelCostOption,
    TravelDeparture,
    TravelEvent,
    TravelSignedPermissionForm,
    TravelSignup,
)
from travel.views import (
    TravelSignupApproveView,
    TravelSignupDeclineView,
    TravelSignupUndoView,
)

PDF_CONTENT = b"%PDF-1.4\n% signed permission form for a test student\n%%EOF\n"
TEST_FILE_KEY = "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg="


@override_settings(
    FILE_ENCRYPTION_KEY=TEST_FILE_KEY,
    ALLOWED_HOSTS=[".localhost", "testserver"],
)
class SignedPermissionFormTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        settings_override = override_settings(MEDIA_ROOT=self.media.name)
        settings_override.enable()
        self.addCleanup(settings_override.disable)

        self.organization = Organization.objects.get(slug="gos")
        self.foreign_organization = Organization.objects.create(
            name="Other Travel Organization",
            slug="other-travel-signed",
            subdomain="other-travel-signed",
        )
        self.program = Program.objects.create(
            organization=self.organization, name="Signed Forms Program"
        )
        feature, _ = ProgramFeature.objects.get_or_create(
            key="travel", defaults={"name": "Travel"}
        )
        self.program.features.add(feature)
        self.school = School.objects.create(name="Signed Forms School")

        self.lead = User.objects.create_user(username="signed-lead")
        UserOrganizationMembership.objects.create(
            user=self.lead,
            organization=self.organization,
            role="LeadMentor",
            is_active=True,
        )
        self.mentor = User.objects.create_user(username="signed-mentor")
        Adult.objects.create(
            organization=self.organization,
            user=self.mentor,
            is_mentor=True,
            mentor_active=True,
            login_enabled=True,
        )
        self.student_user = User.objects.create_user(username="signed-student")
        self.student = Student.objects.create(
            organization=self.organization,
            user=self.student_user,
            legal_first_name="Avery",
            last_name="Traveler",
            school=self.school,
            graduation_year=2027,
        )
        Enrollment.objects.create(
            organization=self.organization,
            student=self.student,
            program=self.program,
            active=True,
        )
        self.guardian = User.objects.create_user(username="signed-guardian")
        self.guardian_adult = Adult.objects.create(
            organization=self.organization,
            user=self.guardian,
            is_parent=True,
            login_enabled=True,
        )
        self.relationship = AdultStudentRelationship.objects.create(
            organization=self.organization,
            adult=self.guardian_adult,
            student=self.student,
        )
        self.other_guardian = User.objects.create_user(username="unlinked-guardian")
        Adult.objects.create(
            organization=self.organization,
            user=self.other_guardian,
            is_parent=True,
            login_enabled=True,
        )
        self.foreign_lead = User.objects.create_user(username="foreign-signed-lead")
        UserOrganizationMembership.objects.create(
            user=self.foreign_lead,
            organization=self.foreign_organization,
            role="LeadMentor",
            is_active=True,
        )

        start = timezone.localdate() + timedelta(days=30)
        self.event = TravelEvent.objects.create(
            organization=self.organization,
            program=self.program,
            name="Signed Forms Trip",
            start_date=start,
            end_date=start + timedelta(days=2),
            permission_form="travel/forms/blank.pdf",
        )
        self.departure = TravelDeparture.objects.create(
            organization=self.organization,
            event=self.event,
            label="First bus",
            leave_date=start,
        )
        self.cost = TravelCostOption.objects.create(
            organization=self.organization,
            event=self.event,
            name="Optional hotel",
            amount="50.00",
            is_required=False,
        )
        self.signup = TravelSignup.objects.create(
            organization=self.organization,
            event=self.event,
            student=self.student,
            departure=self.departure,
        )

    def upload_url(self, signup=None, program=None):
        return reverse(
            "travel:signup_signed_form_upload",
            args=[(program or self.program).pk, (signup or self.signup).pk],
        )

    def download_url(self, signed_form, program=None):
        return reverse(
            "travel:signup_signed_form_download",
            args=[(program or self.program).pk, signed_form.pk],
        )

    def upload(
        self,
        user,
        name="signed.pdf",
        content=PDF_CONTENT,
        content_type="application/pdf",
    ):
        self.client.force_login(user)
        return self.client.post(
            self.upload_url(),
            {
                "file": SimpleUploadedFile(name, content, content_type=content_type),
                "form_version": str(self.event.permission_form_version),
            },
        )

    def approve(self):
        self.client.force_login(self.guardian)
        self.signup.refresh_from_db()
        return self.client.post(
            reverse("travel:signup_approve", args=[self.program.pk, self.signup.pk]),
            {
                "permission_acknowledged": "1",
                "approval_token": self.signup.approval_token,
            },
        )

    def event_edit_payload(self, departures):
        payload = {
            "name": self.event.name,
            "start_date": self.event.start_date.isoformat(),
            "end_date": self.event.end_date.isoformat(),
            "location_name": self.event.location_name,
            "location_address": self.event.location_address,
            "description": self.event.description,
            "departures-TOTAL_FORMS": str(len(departures)),
            "departures-INITIAL_FORMS": str(len(departures)),
            "departures-MIN_NUM_FORMS": "0",
            "departures-MAX_NUM_FORMS": "1000",
            "costs-TOTAL_FORMS": "1",
            "costs-INITIAL_FORMS": "1",
            "costs-MIN_NUM_FORMS": "0",
            "costs-MAX_NUM_FORMS": "1000",
            "costs-0-id": str(self.cost.pk),
            "costs-0-name": self.cost.name,
            "costs-0-amount": str(self.cost.amount),
            "costs-0-unit_label": self.cost.unit_label,
            "costs-0-notes": self.cost.notes,
            "costs-0-sort_order": str(self.cost.sort_order),
        }
        for index, departure in enumerate(departures):
            prefix = f"departures-{index}"
            payload.update(
                {
                    f"{prefix}-id": str(departure.pk),
                    f"{prefix}-label": departure.label,
                    f"{prefix}-leave_date": departure.leave_date.isoformat(),
                    f"{prefix}-leave_time": "",
                    f"{prefix}-meeting_location": departure.meeting_location,
                    f"{prefix}-notes": departure.notes,
                }
            )
        return payload

    def test_guardian_and_lead_upload_record_version_without_approving(self):
        self.assertIsInstance(self.event.permission_form_version, UUID)
        for user in (self.guardian, self.lead):
            with self.subTest(user=user.username):
                previous_ids = set(
                    self.signup.signed_permission_forms.values_list("pk", flat=True)
                )
                self.upload(user)
                records = self.signup.signed_permission_forms.exclude(
                    pk__in=previous_ids
                )
                self.assertEqual(records.count(), 1)
                signed = records.get()
                self.assertEqual(signed.organization, self.organization)
                self.assertEqual(signed.signup, self.signup)
                self.assertEqual(
                    signed.form_version, self.event.permission_form_version
                )
                self.assertEqual(signed.uploaded_by, user)
                self.assertIsNotNone(signed.uploaded_at)
                self.signup.refresh_from_db()
                self.assertEqual(self.signup.status, TravelSignup.INTERESTED)
                self.assertIsNone(self.signup.approved_by)
                self.assertFalse(self.signup.permission_acknowledged)

        self.approve()
        self.signup.refresh_from_db()
        self.assertEqual(self.signup.status, TravelSignup.APPROVED)
        self.assertEqual(self.signup.approved_by, self.guardian_adult)

    def test_signed_form_upload_notifies_family_without_approving(self):
        self.student_user.email = "signed-student@example.test"
        self.student_user.save(update_fields=["email"])
        self.guardian_adult.personal_email = "signed-guardian@example.test"
        self.guardian_adult.email_updates = True
        self.guardian_adult.save(update_fields=["personal_email", "email_updates"])

        with self.captureOnCommitCallbacks(execute=True):
            response = self.upload(self.guardian)

        self.assertEqual(response.status_code, 302)
        self.signup.refresh_from_db()
        self.assertEqual(self.signup.status, TravelSignup.INTERESTED)
        self.assertEqual(
            {message.to[0] for message in mail.outbox},
            {"signed-student@example.test", "signed-guardian@example.test"},
        )
        self.assertTrue(
            all("does not approve" in message.body for message in mail.outbox)
        )

    def test_signed_file_is_encrypted_and_download_is_protected_attachment(self):
        self.upload(self.guardian)
        signed = self.signup.signed_permission_forms.get()
        with default_storage.open(signed.file.name, "rb") as stored:
            ciphertext = stored.read()
        self.assertNotEqual(ciphertext, PDF_CONTENT)
        self.assertNotIn(PDF_CONTENT, ciphertext)
        self.assertTrue(signed.file.name.startswith("private/"))
        self.assertEqual(self.client.get(signed.file.url).status_code, 404)
        with signed.file.open("rb") as decrypted:
            self.assertEqual(decrypted.read(), PDF_CONTENT)

        for user in (self.guardian, self.lead):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(self.download_url(signed))
                self.assertEqual(response.status_code, 200)
                self.assertIn("attachment", response["Content-Disposition"].lower())
                self.assertIn("no-store", response["Cache-Control"].lower())
                self.assertEqual(b"".join(response.streaming_content), PDF_CONTENT)

        for user in (
            self.other_guardian,
            self.student_user,
            self.mentor,
            self.foreign_lead,
        ):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(self.download_url(signed))
                self.assertNotEqual(response.status_code, 200)
                self.assertNotIn(PDF_CONTENT, response.content)

        self.client.logout()
        response = self.client.get(self.download_url(signed))
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn(PDF_CONTENT, response.content)

    def test_unauthorized_uploads_do_not_create_or_change_records(self):
        for user in (
            self.other_guardian,
            self.student_user,
            self.mentor,
            self.foreign_lead,
        ):
            with self.subTest(user=user.username):
                self.upload(user)
                self.assertFalse(self.signup.signed_permission_forms.exists())
                self.signup.refresh_from_db()
                self.assertEqual(self.signup.status, TravelSignup.INTERESTED)
                self.assertIsNone(self.signup.approved_by)

        self.client.logout()
        self.client.post(
            self.upload_url(),
            {
                "file": SimpleUploadedFile(
                    "signed.pdf", PDF_CONTENT, "application/pdf"
                ),
                "form_version": str(self.event.permission_form_version),
            },
        )
        self.assertFalse(self.signup.signed_permission_forms.exists())

    def test_upload_and_download_reject_inconsistent_organization_links(self):
        self.upload(self.guardian)
        signed = self.signup.signed_permission_forms.get()
        url = self.download_url(signed)
        for model, pk in (
            (TravelSignup, self.signup.pk),
            (TravelEvent, self.event.pk),
            (Student, self.student.pk),
        ):
            with self.subTest(model=model.__name__):
                model.objects.filter(pk=pk).update(
                    organization=self.foreign_organization
                )
                try:
                    self.upload(self.lead)
                    self.assertEqual(TravelSignedPermissionForm.objects.count(), 1)
                    self.client.force_login(self.lead)
                    response = self.client.get(url)
                    self.assertNotEqual(response.status_code, 200)
                    self.assertNotIn(PDF_CONTENT, response.content)
                finally:
                    model.objects.filter(pk=pk).update(organization=self.organization)

        TravelSignedPermissionForm.objects.filter(pk=signed.pk).update(
            organization=self.foreign_organization
        )
        self.client.force_login(self.lead)
        response = self.client.get(url)
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn(PDF_CONTENT, response.content)

    def test_foreign_guardian_relationship_does_not_authorize_upload_or_download(self):
        self.upload(self.lead)
        signed = self.signup.signed_permission_forms.get()
        self.relationship.organization = self.foreign_organization
        self.relationship.save(update_fields=["organization"])
        self.upload(self.guardian)
        self.assertEqual(self.signup.signed_permission_forms.count(), 1)
        response = self.client.get(self.download_url(signed))
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn(PDF_CONTENT, response.content)

    def test_upload_requires_event_form_and_post_file(self):
        self.event.permission_form = ""
        self.event.save(update_fields=["permission_form"])
        self.upload(self.guardian)
        self.assertFalse(self.signup.signed_permission_forms.exists())

        self.event.permission_form = "travel/forms/blank.pdf"
        self.event.save(update_fields=["permission_form"])
        self.client.force_login(self.guardian)
        self.client.get(self.upload_url())
        self.client.post(
            self.upload_url(), {"form_version": str(self.event.permission_form_version)}
        )
        self.assertFalse(self.signup.signed_permission_forms.exists())

    def test_upload_rejects_missing_invalid_and_replaced_form_versions(self):
        original_version = str(self.event.permission_form_version)
        self.event.permission_form = "travel/forms/replacement.pdf"
        self.event.save(update_fields=["permission_form"])
        self.assertNotEqual(str(self.event.permission_form_version), original_version)

        for user in (self.guardian, self.lead):
            self.client.force_login(user)
            for version in (None, "", "not-a-uuid", original_version):
                with self.subTest(user=user.username, version=version):
                    payload = {
                        "file": SimpleUploadedFile(
                            "signed.pdf", PDF_CONTENT, "application/pdf"
                        ),
                    }
                    if version is not None:
                        payload["form_version"] = version
                    response = self.client.post(self.upload_url(), payload)
                    self.assertLess(response.status_code, 500)
                    self.assertFalse(self.signup.signed_permission_forms.exists())
                    self.signup.refresh_from_db()
                    self.assertEqual(self.signup.status, TravelSignup.INTERESTED)
                    self.assertFalse(self.signup.permission_acknowledged)

        self.upload(self.guardian)
        signed = self.signup.signed_permission_forms.get()
        self.assertEqual(signed.form_version, self.event.permission_form_version)

    def test_pdf_png_and_jpeg_uploads_are_accepted(self):
        examples = [("signed.pdf", PDF_CONTENT, "application/pdf")]
        for image_format, extension, content_type in (
            ("PNG", "png", "image/png"),
            ("JPEG", "jpg", "image/jpeg"),
        ):
            output = BytesIO()
            Image.new("RGB", (2, 2), "white").save(output, format=image_format)
            examples.append((f"signed.{extension}", output.getvalue(), content_type))

        for name, content, content_type in examples:
            with self.subTest(name=name):
                before = self.signup.signed_permission_forms.count()
                self.upload(self.guardian, name, content, content_type)
                self.assertEqual(
                    self.signup.signed_permission_forms.count(), before + 1
                )
                signed = self.signup.signed_permission_forms.latest("uploaded_at")
                with signed.file.open("rb") as decrypted:
                    self.assertEqual(decrypted.read(), content)

    def test_empty_disallowed_and_oversized_files_are_rejected(self):
        invalid_files = (
            ("empty.pdf", b"", "application/pdf"),
            ("notes.txt", b"not a permission form", "text/plain"),
            ("large.pdf", b"%PDF-1.4\n" + b"x" * (30 * 1024 * 1024), "application/pdf"),
        )
        for name, content, content_type in invalid_files:
            with self.subTest(name=name):
                self.upload(self.guardian, name, content, content_type)
                self.assertFalse(self.signup.signed_permission_forms.exists())

    def test_version_survives_choice_edit_and_reapproval_but_not_form_replacement(self):
        second_departure = TravelDeparture.objects.create(
            organization=self.organization,
            event=self.event,
            label="Second bus",
            leave_date=self.event.start_date + timedelta(days=1),
        )
        self.upload(self.guardian)
        signed = self.signup.signed_permission_forms.get()
        original_version = self.event.permission_form_version
        self.approve()
        self.signup.refresh_from_db()
        self.assertEqual(self.signup.status, TravelSignup.APPROVED)

        self.client.force_login(self.student_user)
        self.client.post(
            reverse("travel:signup_edit", args=[self.program.pk, self.signup.pk]),
            {"departure": second_departure.pk, "cost_items": [self.cost.pk]},
        )
        self.signup.refresh_from_db()
        self.event.refresh_from_db()
        self.assertEqual(self.signup.departure, second_departure)
        self.assertEqual(self.signup.status, TravelSignup.INTERESTED)
        self.assertEqual(self.event.permission_form_version, original_version)
        self.assertTrue(
            self.signup.signed_permission_forms.filter(
                pk=signed.pk, form_version=self.event.permission_form_version
            ).exists()
        )
        self.approve()
        self.signup.refresh_from_db()
        self.assertEqual(self.signup.status, TravelSignup.APPROVED)
        self.assertTrue(
            self.signup.signed_permission_forms.filter(
                pk=signed.pk, form_version=self.event.permission_form_version
            ).exists()
        )
        self.assertFalse(self.signup.needs_permission_acknowledgment)

        payload = self.event_edit_payload([self.departure, second_departure])
        payload["permission_form"] = SimpleUploadedFile(
            "replacement.pdf", b"%PDF-1.4\nreplacement form\n%%EOF\n", "application/pdf"
        )
        self.client.force_login(self.lead)
        self.client.post(
            reverse("travel:event_edit", args=[self.program.pk, self.event.pk]), payload
        )
        self.event.refresh_from_db()
        self.assertNotEqual(self.event.permission_form_version, original_version)
        self.signup.refresh_from_db()
        self.assertTrue(self.signup.needs_permission_acknowledgment)
        self.assertFalse(
            self.signup.signed_permission_forms.filter(
                form_version=self.event.permission_form_version
            ).exists()
        )
        self.upload(self.guardian)
        self.assertTrue(
            self.signup.signed_permission_forms.filter(
                form_version=self.event.permission_form_version
            ).exists()
        )

    def test_roster_tracks_each_students_signed_form_without_exposing_it(self):
        other_student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Zoe",
            last_name="Traveler",
            school=self.school,
            graduation_year=2027,
        )
        Enrollment.objects.create(
            organization=self.organization,
            student=other_student,
            program=self.program,
            active=True,
        )
        AdultStudentRelationship.objects.create(
            organization=self.organization,
            adult=self.other_guardian.adult_profile,
            student=other_student,
        )
        other_signup = TravelSignup.objects.create(
            organization=self.organization,
            event=self.event,
            student=other_student,
            departure=self.departure,
            status=TravelSignup.APPROVED,
            permission_acknowledged=True,
            permission_acknowledged_version=self.event.permission_form_version,
        )
        self.upload(self.guardian)
        signed = self.signup.signed_permission_forms.get()
        self.approve()
        self.signup.refresh_from_db()
        self.assertEqual(self.signup.status, TravelSignup.APPROVED)

        url = reverse("travel:event_list", args=[self.program.pk])
        self.client.force_login(self.lead)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        event = next(
            e for e in response.context["upcoming_events"] if e.pk == self.event.pk
        )
        signups = {signup.pk: signup for signup in event.signups.all()}
        self.assertFalse(signups[self.signup.pk].needs_permission_acknowledgment)
        self.assertTrue(signups[other_signup.pk].needs_permission_acknowledgment)
        self.assertContains(response, "Signed permission form</li>", count=1)

        for user in (self.student_user, self.mentor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, self.download_url(signed))
                self.assertNotContains(response, "Outstanding trip requirements")
        self.client.force_login(self.guardian)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, other_student.display_name)
        self.client.force_login(self.other_guardian)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.student.display_name)
        self.assertNotContains(response, self.download_url(signed))

    def test_past_trip_allows_guardian_to_upload_and_approve(self):
        self.event.start_date = timezone.localdate() - timedelta(days=10)
        self.event.end_date = timezone.localdate() - timedelta(days=8)
        self.event.save()
        self.client.force_login(self.guardian)
        response = self.client.get(reverse("travel:event_list", args=[self.program.pk]))
        self.assertContains(response, self.upload_url())
        self.assertContains(
            response,
            reverse("travel:signup_approve", args=[self.program.pk, self.signup.pk]),
        )
        self.upload(self.guardian)
        self.approve()
        self.signup.refresh_from_db()
        self.assertEqual(self.signup.status, TravelSignup.APPROVED)

    def test_reapproval_does_not_repeat_acknowledgment_after_signed_upload(self):
        self.upload(self.guardian)
        self.approve()
        self.signup.refresh_from_db()
        self.signup.status = TravelSignup.INTERESTED
        self.signup.save()
        self.client.force_login(self.guardian)
        self.client.post(
            reverse("travel:signup_approve", args=[self.program.pk, self.signup.pk]),
            {"approval_token": self.signup.approval_token},
        )
        self.signup.refresh_from_db()
        self.assertEqual(self.signup.status, TravelSignup.APPROVED)
        self.assertEqual(
            self.signup.permission_acknowledged_version,
            self.event.permission_form_version,
        )

    def test_staff_cost_change_requires_reapproval_without_new_signed_form(self):
        self.signup.cost_items.add(self.cost)
        self.upload(self.guardian)
        signed = self.signup.signed_permission_forms.get()
        self.approve()
        payload = self.event_edit_payload([self.departure])
        payload["costs-0-amount"] = "75.00"
        self.client.force_login(self.lead)
        self.client.post(
            reverse("travel:event_edit", args=[self.program.pk, self.event.pk]), payload
        )
        self.signup.refresh_from_db()
        self.assertEqual(self.signup.status, TravelSignup.INTERESTED)
        self.assertFalse(self.signup.needs_permission_acknowledgment)
        self.assertEqual(self.signup.current_signed_permission_form.pk, signed.pk)
        self.client.force_login(self.guardian)
        self.client.post(
            reverse("travel:signup_approve", args=[self.program.pk, self.signup.pk]),
            {"approval_token": self.signup.approval_token},
        )
        self.signup.refresh_from_db()
        self.assertEqual(self.signup.status, TravelSignup.APPROVED)
        self.assertEqual(str(self.signup.approved_total), "75.00")
        self.assertEqual(self.signup.signed_permission_forms.count(), 1)

    def test_staff_departure_time_change_requires_reapproval_without_new_form(self):
        self.student_user.email = "signed-student@example.test"
        self.student_user.save(update_fields=["email"])
        self.guardian_adult.personal_email = "signed-guardian@example.test"
        self.guardian_adult.email_updates = True
        self.guardian_adult.save(update_fields=["personal_email", "email_updates"])
        self.signup.cost_items.add(self.cost)
        self.upload(self.guardian)
        signed = self.signup.signed_permission_forms.get()
        self.approve()
        mail.outbox.clear()
        payload = self.event_edit_payload([self.departure])
        payload["departures-0-leave_time"] = "14:30"
        self.client.force_login(self.lead)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("travel:event_edit", args=[self.program.pk, self.event.pk]),
                payload,
            )

        self.assertEqual(response.status_code, 302)
        self.signup.refresh_from_db()
        self.assertEqual(self.signup.status, TravelSignup.INTERESTED)
        self.assertIsNone(self.signup.approved_total)
        self.assertEqual(str(self.signup.balance_amount), "50.00")
        self.assertEqual(self.signup.current_signed_permission_form.pk, signed.pk)
        self.assertFalse(self.signup.needs_permission_acknowledgment)
        self.assertEqual(
            {message.to[0] for message in mail.outbox},
            {"signed-student@example.test", "signed-guardian@example.test"},
        )
        self.assertTrue(
            all("approval" in message.body.lower() for message in mail.outbox)
        )

    def test_trip_deletion_removes_signed_form_files_after_commit(self):
        self.upload(self.guardian)
        signed = self.signup.signed_permission_forms.get()
        name = signed.file.name
        self.assertTrue(default_storage.exists(name))
        self.client.force_login(self.lead)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("travel:event_delete", args=[self.program.pk, self.event.pk])
            )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TravelEvent.objects.filter(pk=self.event.pk).exists())
        self.assertFalse(default_storage.exists(name))

    def test_parent_decisions_reject_foreign_relationships_and_signups(self):
        self.client.force_login(self.guardian)
        cases = (
            ("signup_approve", TravelSignup.INTERESTED, TravelSignupApproveView),
            ("signup_decline", TravelSignup.INTERESTED, TravelSignupDeclineView),
            ("signup_undo", TravelSignup.APPROVED, TravelSignupUndoView),
        )
        for action, status, view_class in cases:
            with self.subTest(action=action, inconsistency="relationship"):
                self.signup.status = status
                self.signup.save()
                AdultStudentRelationship.objects.filter(pk=self.relationship.pk).update(
                    organization=self.foreign_organization
                )
                self.client.post(
                    reverse(f"travel:{action}", args=[self.program.pk, self.signup.pk]),
                    {
                        "permission_acknowledged": "1",
                        **(
                            {"approval_token": self.signup.approval_token}
                            if action == "signup_approve"
                            else {}
                        ),
                    },
                )
                self.signup.refresh_from_db()
                self.assertEqual(self.signup.status, status)
                request = RequestFactory().post("/")
                request.user = self.guardian
                request.organization = self.organization
                view = view_class()
                view.request = request
                self.assertFalse(view._may_act(self.guardian, self.signup))
                AdultStudentRelationship.objects.filter(pk=self.relationship.pk).update(
                    organization=self.organization
                )
            with self.subTest(action=action, inconsistency="event"):
                self.client.force_login(self.guardian)
                TravelEvent.objects.filter(pk=self.event.pk).update(
                    organization=self.foreign_organization
                )
                response = self.client.post(
                    reverse(f"travel:{action}", args=[self.program.pk, self.signup.pk]),
                    {"permission_acknowledged": "1"},
                )
                self.assertNotEqual(response.status_code, 200)
                request = RequestFactory().post("/")
                request.user = self.guardian
                request.organization = self.organization
                with self.assertRaises(Http404):
                    view_class.as_view()(
                        request, program_id=self.program.pk, pk=self.signup.pk
                    )
                self.signup.refresh_from_db()
                self.assertEqual(self.signup.status, status)
                TravelEvent.objects.filter(pk=self.event.pk).update(
                    organization=self.organization
                )
