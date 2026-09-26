import datetime
from io import StringIO

from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from applications.models import Application
from programs.models import Adult, Student


class AnonymizeEmailsCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="realstudent",
            email="real_student@gmail.com",
        )
        self.email_addr = EmailAddress.objects.create(
            user=self.user,
            email="real_student@gmail.com",
            primary=True,
            verified=True,
        )
        self.student = Student.objects.create(
            legal_first_name="Alice",
            last_name="Smith",
            date_of_birth=datetime.date(2008, 5, 10),
            personal_email="real_student@gmail.com",
            andrew_email="asmith@andrew.cmu.edu",
            user=self.user,
        )
        self.adult = Adult.objects.create(
            legal_first_name="Bob",
            last_name="Smith",
            personal_email="bob.smith@company.com",
            andrew_email="bsmith@andrew.cmu.edu",
            is_parent=True,
        )
        self.application = Application.objects.create(
            email="applicant@yahoo.com",
            applicant_type="student",
            data={
                "step5-student": {
                    "personal_email": "applicant@yahoo.com",
                }
            },
        )

    def test_dry_run_makes_no_changes(self):
        out = StringIO()
        call_command(
            "anonymize_emails", "--dry-run", "--domain=dev.invalid", stdout=out
        )
        output = out.getvalue()

        self.assertIn("[DRY-RUN]", output)

        self.student.refresh_from_db()
        self.assertEqual(self.student.personal_email, "real_student@gmail.com")
        self.assertEqual(self.student.andrew_email, "asmith@andrew.cmu.edu")

        self.adult.refresh_from_db()
        self.assertEqual(self.adult.personal_email, "bob.smith@company.com")

        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "real_student@gmail.com")

    def test_anonymize_without_force_or_dry_run_prompts_or_warns(self):
        out = StringIO()
        call_command("anonymize_emails", stdout=out)
        output = out.getvalue()
        self.assertIn("specify --force", output)

        self.student.refresh_from_db()
        self.assertEqual(self.student.personal_email, "real_student@gmail.com")

    def test_anonymize_executes_with_force(self):
        out = StringIO()
        call_command("anonymize_emails", "--force", "--domain=example.test", stdout=out)

        self.student.refresh_from_db()
        self.assertEqual(
            self.student.personal_email, f"student_{self.student.pk}@example.test"
        )
        self.assertEqual(
            self.student.andrew_email, f"student_{self.student.pk}_andrew@example.test"
        )

        self.adult.refresh_from_db()
        self.assertEqual(
            self.adult.personal_email, f"adult_{self.adult.pk}@example.test"
        )
        self.assertEqual(
            self.adult.andrew_email, f"adult_{self.adult.pk}_andrew@example.test"
        )

        self.user.refresh_from_db()
        self.assertTrue(self.user.email.endswith("@example.test"))

        self.email_addr.refresh_from_db()
        self.assertEqual(self.email_addr.email, self.user.email)

        self.application.refresh_from_db()
        self.assertEqual(
            self.application.email, f"application_{self.application.pk}@example.test"
        )
