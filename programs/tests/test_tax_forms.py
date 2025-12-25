from django.contrib.auth.models import Permission, User
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from programs.models import Adult, Enrollment, Program, SlidingScale, Student, TaxForm


class SlidingScaleTaxFormTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(
            name="Test Program", year=2025, active=True
        )
        self.student = Student.objects.create(
            legal_first_name="Test",
            last_name="Student",
            personal_email="student@example.com",
        )
        Enrollment.objects.create(student=self.student, program=self.program)

        self.mentor_user = User.objects.create_user(
            username="mentor", password="password", email="mentor@example.com"  # nosec
        )
        # Give mentor permissions
        content_type = ContentType.objects.get_for_model(SlidingScale)
        permission = Permission.objects.get(
            codename="change_slidingscale", content_type=content_type
        )
        self.mentor_user.user_permissions.add(permission)

        # Parent user (Student's parent)
        self.parent_adult = Adult.objects.create(
            first_name="Parent", last_name="User", personal_email="parent@example.com"
        )
        self.student.primary_contact = self.parent_adult
        self.student.save()

        self.parent_user = User.objects.create_user(
            username="parent", password="password", email="parent@example.com"  # nosec
        )

    def test_mentor_can_upload_and_delete_tax_form(self):
        self.client.login(username="mentor", password="password")  # nosec

        # Create sliding scale
        ss = SlidingScale.objects.create(
            student=self.student, program=self.program, percent=10, date="2025-01-01"
        )

        # Upload tax form via parent upload view (it allows mentors too now)
        pdf_content = b"fake pdf content"
        tax_form = SimpleUploadedFile(
            "tax_form.pdf", pdf_content, content_type="application/pdf"
        )

        url = reverse(
            "program_sliding_scale_parent_upload",
            kwargs={"pk": self.program.pk, "student_id": self.student.pk},
        )
        response = self.client.post(
            url,
            {
                "tax_form": tax_form,
            },
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        ss.refresh_from_db()
        self.assertEqual(ss.tax_forms.count(), 1)
        form_obj = ss.tax_forms.first()
        # Django might add a suffix if file exists, so check if it contains the name
        self.assertIn("tax_form", form_obj.file.name)
        self.assertTrue(form_obj.file.name.endswith(".pdf"))

        # Now delete it
        delete_url = reverse(
            "program_sliding_scale_delete_tax_form",
            kwargs={"pk": self.program.pk, "sliding_id": ss.pk, "form_id": form_obj.pk},
        )
        response = self.client.post(delete_url, follow=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ss.tax_forms.count(), 0)

    def test_unauthorized_access(self):
        ss = SlidingScale.objects.create(
            student=self.student, program=self.program, percent=10
        )
        tf = TaxForm.objects.create(
            sliding_scale=ss, file=SimpleUploadedFile("t.pdf", b"c")
        )

        # Try to delete without login
        delete_url = reverse(
            "program_sliding_scale_delete_tax_form",
            kwargs={"pk": self.program.pk, "sliding_id": ss.pk, "form_id": tf.pk},
        )
        response = self.client.post(delete_url)
        self.assertEqual(response.status_code, 302)  # Redirect to login

    def test_parent_upload_tax_form(self):
        # Create a user with Adult profile
        parent_user = User.objects.create_user(
            username="parent_user", password="password"  # nosec
        )
        self.parent_adult.user = parent_user
        self.parent_adult.is_parent = True
        self.parent_adult.save()
        self.parent_adult.students.add(self.student)

        self.client.login(username="parent_user", password="password")  # nosec

        pdf_content = b"parent pdf content"
        tax_form = SimpleUploadedFile(
            "parent_tax_form.pdf", pdf_content, content_type="application/pdf"
        )

        url = reverse(
            "program_sliding_scale_parent_upload",
            kwargs={"pk": self.program.pk, "student_id": self.student.pk},
        )
        response = self.client.post(url, {"tax_form": tax_form}, follow=False)

        self.assertEqual(response.status_code, 302)

        ss = SlidingScale.objects.get(student=self.student, program=self.program)
        self.assertEqual(ss.tax_forms.count(), 1)
        self.assertIn("parent_tax_form", ss.tax_forms.first().file.name)
        self.assertTrue(ss.is_pending)

    def test_parent_cannot_upload_for_other_student(self):
        other_student = Student.objects.create(
            legal_first_name="Other", last_name="Student"
        )

        parent_user = User.objects.create_user(
            username="parent_user_2", password="password"  # nosec
        )
        # Reuse self.parent_adult but link to new user and ensure NO student link to other_student
        self.parent_adult.user = parent_user
        self.parent_adult.is_parent = True
        self.parent_adult.save()
        self.parent_adult.students.clear()  # IMPORTANT: clear students

        self.client.login(username="parent_user_2", password="password")  # nosec

        pdf_content = b"parent pdf content"
        tax_form = SimpleUploadedFile(
            "parent_tax_form.pdf", pdf_content, content_type="application/pdf"
        )

        url = reverse(
            "program_sliding_scale_parent_upload",
            kwargs={"pk": self.program.pk, "student_id": other_student.pk},
        )
        response = self.client.post(url, {"tax_form": tax_form}, follow=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))
        self.assertFalse(
            SlidingScale.objects.filter(
                student=other_student, program=self.program
            ).exists()
        )

    def test_multiple_uploads(self):
        parent_user = User.objects.create_user(
            username="parent_user_multi", password="password"  # nosec
        )
        self.parent_adult.user = parent_user
        self.parent_adult.is_parent = True
        self.parent_adult.save()
        self.parent_adult.students.add(self.student)

        self.client.login(username="parent_user_multi", password="password")  # nosec

        url = reverse(
            "program_sliding_scale_parent_upload",
            kwargs={"pk": self.program.pk, "student_id": self.student.pk},
        )

        # Upload 1
        tax_form1 = SimpleUploadedFile(
            "form1.pdf", b"content1", content_type="application/pdf"
        )
        self.client.post(url, {"tax_form": tax_form1}, follow=False)

        # Upload 2
        tax_form2 = SimpleUploadedFile(
            "form2.pdf", b"content2", content_type="application/pdf"
        )
        self.client.post(url, {"tax_form": tax_form2}, follow=False)

        ss = SlidingScale.objects.get(student=self.student, program=self.program)
        self.assertEqual(ss.tax_forms.count(), 2)

    def test_encryption(self):
        self.client.login(username="mentor", password="password")  # nosec
        ss = SlidingScale.objects.create(
            student=self.student, program=self.program, percent=10
        )

        content = b"highly sensitive data"
        tax_form = SimpleUploadedFile(
            "secret.pdf", content, content_type="application/pdf"
        )

        url = reverse(
            "program_sliding_scale_parent_upload",
            kwargs={"pk": self.program.pk, "student_id": self.student.pk},
        )
        self.client.post(url, {"tax_form": tax_form}, follow=False)

        ss.refresh_from_db()
        form_obj = ss.tax_forms.first()
        file_path = form_obj.file.path

        with open(file_path, "rb") as f:
            disk_content = f.read()

        self.assertNotEqual(
            disk_content, content, "File content on disk should be encrypted"
        )
        self.assertNotIn(
            content, disk_content, "Plaintext should not be in the encrypted file"
        )

        # Check decryption
        with form_obj.file.open("rb") as f:
            decrypted_content = f.read()
        self.assertEqual(
            decrypted_content, content, "Decrypted content should match original"
        )
