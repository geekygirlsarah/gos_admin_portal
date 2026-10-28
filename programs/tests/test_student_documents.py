import os

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Program,
    ProgramDocument,
    Student,
    StudentDocument,
)

User = get_user_model()


class StudentDocumentEncryptionTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Doc Program")
        self.student = Student.objects.create(
            legal_first_name="Alice", last_name="Smith"
        )
        self.prog_doc = ProgramDocument.objects.create(
            program=self.program,
            name="Liability Waiver",
            file=SimpleUploadedFile("waiver_template.pdf", b"template content"),
        )

    def test_student_document_file_is_encrypted_on_storage(self):
        content = b"%PDF-1.4 signed waiver secret content 12345"
        uploaded = SimpleUploadedFile("signed_waiver.pdf", content)
        doc = StudentDocument.objects.create(
            student=self.student,
            program_document=self.prog_doc,
            file=uploaded,
        )

        # Raw file stored on disk should NOT contain the plaintext bytes directly
        with default_storage.open(doc.file.name, "rb") as fh:
            raw_bytes = fh.read()

        self.assertNotEqual(raw_bytes, content)
        # But reading via doc.file.open() should decrypt and return original content
        with doc.file.open("rb") as fh:
            decrypted = fh.read()
        self.assertEqual(decrypted, content)


class StudentDocumentDownloadViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")

        self.program = Program.objects.create(name="Doc Program", active=True)
        self.student = Student.objects.create(
            legal_first_name="Alice", last_name="Smith"
        )
        self.enrollment = Enrollment.objects.create(
            student=self.student, program=self.program, active=True
        )

        self.prog_doc = ProgramDocument.objects.create(
            program=self.program,
            name="Liability Waiver",
            file=SimpleUploadedFile("waiver_template.pdf", b"template content"),
        )
        self.file_content = b"%PDF-1.4 confidential signed waiver bytes"
        self.doc = StudentDocument.objects.create(
            student=self.student,
            program_document=self.prog_doc,
            file=SimpleUploadedFile("alice_waiver.pdf", self.file_content),
        )

        # Users
        self.lead_user = User.objects.create_user(
            username="lead", email="lead@example.com", password="password"  # nosec B106
        )
        self.lead_user.groups.add(self.lead_group)

        self.mentor_user = User.objects.create_user(
            username="mentor",
            email="mentor@example.com",
            password="password",  # nosec B106
        )
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user,
            legal_first_name="Mentor",
            last_name="Dan",
            is_mentor=True,
            personal_email="mentor@example.com",
        )

        self.parent_user = User.objects.create_user(
            username="parent",
            email="parent@example.com",
            password="password",  # nosec B106
        )
        self.parent_adult = Adult.objects.create(
            user=self.parent_user,
            legal_first_name="Carol",
            last_name="Smith",
            is_parent=True,
            personal_email="parent@example.com",
        )
        AdultStudentRelationship.objects.create(
            adult=self.parent_adult,
            student=self.student,
            relationship_to_student="parent",
        )

        self.other_parent_user = User.objects.create_user(
            username="other_parent",
            email="other@example.com",
            password="password",  # nosec B106
        )
        self.other_parent_adult = Adult.objects.create(
            user=self.other_parent_user,
            legal_first_name="Other",
            last_name="Parent",
            is_parent=True,
            personal_email="other@example.com",
        )

        self.student_user = User.objects.create_user(
            username="alice",
            email="alice@example.com",
            password="password",  # nosec B106
        )
        self.student.user = self.student_user
        self.student.save()

        self.other_student_user = User.objects.create_user(
            username="bob",
            email="bob@example.com",
            password="password",  # nosec B106
        )
        self.other_student = Student.objects.create(
            user=self.other_student_user,
            legal_first_name="Bob",
            last_name="Jones",
        )

        self.url = reverse("student_document_download", args=[self.doc.pk])

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_lead_mentor_can_download(self):
        self.client.force_login(self.lead_user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b"".join(resp.streaming_content), self.file_content)

    def test_mentor_can_download(self):
        self.client.force_login(self.mentor_user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b"".join(resp.streaming_content), self.file_content)

    def test_linked_parent_can_download(self):
        self.client.force_login(self.parent_user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b"".join(resp.streaming_content), self.file_content)

    def test_unrelated_parent_forbidden(self):
        self.client.force_login(self.other_parent_user)
        resp = self.client.get(self.url)
        self.assertIn(resp.status_code, (302, 403))

    def test_student_owner_can_download(self):
        self.client.force_login(self.student_user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b"".join(resp.streaming_content), self.file_content)

    def test_other_student_forbidden(self):
        self.client.force_login(self.other_student_user)
        resp = self.client.get(self.url)
        self.assertIn(resp.status_code, (302, 403))
