import shutil
import tempfile

from cryptography.fernet import Fernet
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.signals import post_init
from django.test import TestCase, override_settings

from applications.models import Application, ApplicationDocumentSubmission
from applications.services import (
    ApplicationConversionError,
    convert_application_to_student,
)
from GoSAdminPortal.test_runner import _assign_default_test_org
from organizations.models import Organization
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Program,
    ProgramDocument,
    Student,
    StudentDocument,
)

_MEDIA_ROOT = tempfile.mkdtemp(prefix="application-conversion-isolation-")
_FILE_ENCRYPTION_KEY = Fernet.generate_key().decode()


@override_settings(
    FILE_ENCRYPTION_KEY=_FILE_ENCRYPTION_KEY,
    MEDIA_ROOT=_MEDIA_ROOT,
)
class ApplicationConversionTenantIsolationTests(TestCase):
    tenant_models = (
        Application,
        ApplicationDocumentSubmission,
        Adult,
        AdultStudentRelationship,
        Enrollment,
        Program,
        Student,
    )

    def setUp(self):
        self.disconnected_models = []
        for model in self.tenant_models:
            if post_init.disconnect(
                receiver=_assign_default_test_org,
                sender=model,
                dispatch_uid=f"default-test-org-{model._meta.label}",
            ):
                self.disconnected_models.append(model)
        self.addCleanup(self._restore_default_organization_hook)

        self.alpha = Organization.objects.get(slug="gos")
        self.beta = Organization.objects.create(
            name="Conversion Beta",
            slug="conversion-beta",
            subdomain="conversion-beta",
        )
        self.alpha_program = Program.objects.create(
            organization=self.alpha, name="Alpha Program"
        )
        self.beta_program = Program.objects.create(
            organization=self.beta, name="Beta Program"
        )

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)

    def _restore_default_organization_hook(self):
        for model in self.disconnected_models:
            post_init.connect(
                receiver=_assign_default_test_org,
                sender=model,
                dispatch_uid=f"default-test-org-{model._meta.label}",
            )

    def _student_data(self, email="student@example.test", parent_email=None):
        data = {
            "step5-student": {
                "legal_first_name": "Avery",
                "preferred_first_name": "Avery New",
                "last_name": "Student",
                "personal_email": email,
                "date_of_birth": "2010-01-01",
            }
        }
        if parent_email:
            data["step7-primaryparent"] = {
                "legal_first_name": "Pat",
                "last_name": "Parent",
                "email": parent_email,
            }
        return data

    def _student_application(
        self, data=None, program=None, email="student@example.test"
    ):
        return Application.objects.create(
            organization=self.alpha,
            applicant_type=Application.Type.STUDENT,
            email=email,
            program=program or self.alpha_program,
            status=Application.Status.APPROVED_SIGNED,
            data=data or self._student_data(email=email),
        )

    def test_new_student_parent_relationship_and_enrollment_get_explicit_ownership(
        self,
    ):
        application = self._student_application(
            data=self._student_data(parent_email="parent@example.test")
        )

        student = convert_application_to_student(application)

        self.assertEqual(student.organization_id, self.alpha.pk)
        parent = student.primary_contact
        self.assertEqual(parent.organization_id, self.alpha.pk)
        self.assertTrue(
            AdultStudentRelationship.objects.filter(
                organization=self.alpha, adult=parent, student=student
            ).exists()
        )
        enrollment = Enrollment.objects.get(student=student, program=self.alpha_program)
        self.assertEqual(enrollment.organization_id, self.alpha.pk)

    def test_conversion_reuses_local_identity_without_mutating_foreign_matches(self):
        foreign_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="Avery",
            preferred_first_name=None,
            last_name="Student",
            personal_email="student@example.test",
            date_of_birth="2010-01-01",
        )
        local_student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Avery",
            preferred_first_name="Local Preferred",
            last_name="Student",
            personal_email="student@example.test",
            date_of_birth="2010-01-01",
        )
        foreign_parent = Adult.objects.create(
            organization=self.beta,
            legal_first_name="Pat",
            last_name="Parent",
            personal_email="parent@example.test",
            is_parent=False,
        )
        local_parent = Adult.objects.create(
            organization=self.alpha,
            legal_first_name="Pat",
            last_name="Parent",
            personal_email="parent@example.test",
            is_parent=True,
        )
        application = self._student_application(
            data=self._student_data(parent_email="parent@example.test")
        )

        converted = convert_application_to_student(application)

        self.assertEqual(converted.pk, local_student.pk)
        self.assertEqual(converted.primary_contact_id, local_parent.pk)
        foreign_student.refresh_from_db()
        foreign_parent.refresh_from_db()
        self.assertIsNone(foreign_student.preferred_first_name)
        self.assertFalse(foreign_parent.is_parent)

    def test_mentor_conversion_does_not_update_foreign_adult_match(self):
        foreign_mentor = Adult.objects.create(
            organization=self.beta,
            legal_first_name="Morgan",
            last_name="Mentor",
            personal_email="mentor@example.test",
            is_mentor=False,
        )
        application = Application.objects.create(
            organization=self.alpha,
            applicant_type=Application.Type.MENTOR,
            email="mentor@example.test",
            status=Application.Status.APPROVED_SIGNED,
            data={
                "mentor_info": {
                    "legal_first_name": "Morgan",
                    "last_name": "Mentor",
                }
            },
        )

        converted = convert_application_to_student(application)

        self.assertNotEqual(converted.pk, foreign_mentor.pk)
        self.assertEqual(converted.organization_id, self.alpha.pk)
        self.assertTrue(converted.is_mentor)
        foreign_mentor.refresh_from_db()
        self.assertFalse(foreign_mentor.is_mentor)

    def test_foreign_existing_student_id_is_rejected_without_mutation(self):
        foreign_student = Student.objects.create(
            organization=self.beta,
            legal_first_name="Original",
            last_name="Foreign",
            date_of_birth="2010-01-01",
        )
        application = self._student_application(
            data={
                "step5-student": {
                    "_existing_student_id": foreign_student.pk,
                    "legal_first_name": "Changed",
                    "last_name": "Name",
                    "date_of_birth": "2010-01-01",
                }
            },
            email="",
        )

        with self.assertRaises(ApplicationConversionError):
            convert_application_to_student(application)

        foreign_student.refresh_from_db()
        application.refresh_from_db()
        self.assertEqual(foreign_student.legal_first_name, "Original")
        self.assertEqual(application.status, Application.Status.APPROVED_SIGNED)
        self.assertFalse(Student.objects.filter(organization=self.alpha).exists())

    def test_foreign_program_is_rejected_before_conversion(self):
        application = self._student_application(program=self.beta_program)

        with self.assertRaises(ApplicationConversionError):
            convert_application_to_student(application)

        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.APPROVED_SIGNED)
        self.assertFalse(Student.objects.filter(organization=self.alpha).exists())

    def test_foreign_enrollment_collision_is_rejected_without_reassignment(self):
        student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Avery",
            last_name="Student",
            personal_email="student@example.test",
            date_of_birth="2010-01-01",
        )
        foreign_enrollment = Enrollment.objects.create(
            organization=self.beta, student=student, program=self.alpha_program
        )
        application = self._student_application()

        with self.assertRaises(ApplicationConversionError):
            convert_application_to_student(application)

        foreign_enrollment.refresh_from_db()
        application.refresh_from_db()
        self.assertEqual(foreign_enrollment.organization_id, self.beta.pk)
        self.assertEqual(application.status, Application.Status.APPROVED_SIGNED)

    def test_foreign_owned_relationship_collision_is_rejected_unchanged(self):
        student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Avery",
            last_name="Student",
            personal_email="student@example.test",
            date_of_birth="2010-01-01",
        )
        parent = Adult.objects.create(
            organization=self.alpha,
            legal_first_name="Pat",
            last_name="Parent",
            personal_email="parent@example.test",
            is_parent=True,
        )
        relationship = AdultStudentRelationship.objects.create(
            organization=self.beta,
            adult=parent,
            student=student,
            relationship_to_student="parent",
        )
        application = self._student_application(
            data=self._student_data(parent_email="parent@example.test")
        )

        with self.assertRaises(ApplicationConversionError):
            convert_application_to_student(application)

        relationship.refresh_from_db()
        self.assertEqual(relationship.organization_id, self.beta.pk)

    def test_foreign_document_submission_is_rejected_before_transfer(self):
        foreign_document = ProgramDocument.objects.create(
            program=self.beta_program,
            name="Foreign release",
            file="",
        )
        application = self._student_application()
        submission = ApplicationDocumentSubmission.objects.create(
            organization=self.beta,
            application=application,
            document=foreign_document,
            file="",
        )

        with self.assertRaises(ApplicationConversionError):
            convert_application_to_student(application)

        submission.refresh_from_db()
        self.assertEqual(submission.organization_id, self.beta.pk)
        self.assertFalse(Student.objects.filter(organization=self.alpha).exists())
        self.assertFalse(StudentDocument.objects.exists())

    def test_local_signed_document_still_transfers_to_student(self):
        document = ProgramDocument.objects.create(
            program=self.alpha_program,
            name="Local release",
            file="",
        )
        application = self._student_application()
        ApplicationDocumentSubmission.objects.create(
            organization=self.alpha,
            application=application,
            document=document,
            file=SimpleUploadedFile("signed.pdf", b"signed document bytes"),
        )

        student = convert_application_to_student(application)

        student_document = StudentDocument.objects.get(
            student=student, program_document=document
        )
        with student_document.file.open("rb") as signed_file:
            self.assertEqual(signed_file.read(), b"signed document bytes")
