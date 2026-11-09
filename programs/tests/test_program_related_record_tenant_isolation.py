from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from applications.models import Application, ApplicationDocumentSubmission
from organizations.models import Organization, UserOrganizationMembership
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


class ProgramRelatedRecordTenantIsolationBase(TestCase):
    def setUp(self):
        self.organization = Organization.objects.get(slug="gos")
        self.other_organization = Organization.objects.create(
            name="Other Organization", slug="other-related-records"
        )
        self.program = Program.objects.create(
            name="Local Program", organization=self.organization
        )
        self.other_program = Program.objects.create(
            name="Other Program", organization=self.other_organization
        )
        self.lead_user = User.objects.create_user(
            username="related-records-lead", password="password123"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=self.lead_user,
            organization=self.organization,
            role="LeadMentor",
            is_active=True,
        )
        self.client.force_login(self.lead_user)


class ProgramStudentDocumentsTenantIsolationTests(
    ProgramRelatedRecordTenantIsolationBase
):
    def test_matrix_only_uses_local_enrollments_students_and_signed_documents(self):
        local_document = ProgramDocument.objects.create(
            program=self.program,
            name="Local Waiver",
            is_required=True,
        )
        foreign_document = ProgramDocument.objects.create(
            program=self.other_program,
            name="Foreign Waiver",
            is_required=True,
        )

        local_student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Local",
            last_name="Student",
        )
        Enrollment.objects.create(
            organization=self.organization,
            program=self.program,
            student=local_student,
            active=True,
        )
        local_submission = StudentDocument.objects.create(
            student=local_student,
            program_document=local_document,
            file="signed/local-waiver.pdf",
        )
        foreign_document_submission = StudentDocument.objects.create(
            student=local_student,
            program_document=foreign_document,
            file="signed/foreign-waiver.pdf",
        )

        local_student_foreign_enrollment = Student.objects.create(
            organization=self.organization,
            legal_first_name="Wrong Enrollment",
            last_name="Student",
        )
        Enrollment.objects.create(
            organization=self.other_organization,
            program=self.program,
            student=local_student_foreign_enrollment,
            active=True,
        )

        foreign_student = Student.objects.create(
            organization=self.other_organization,
            legal_first_name="Foreign",
            last_name="Student",
        )
        Enrollment.objects.create(
            organization=self.organization,
            program=self.program,
            student=foreign_student,
            active=True,
        )
        foreign_student_submission = StudentDocument.objects.create(
            student=foreign_student,
            program_document=local_document,
            file="signed/foreign-student-waiver.pdf",
        )

        response = self.client.get(
            reverse("program_student_documents", args=[self.program.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Local Student")
        self.assertNotContains(response, "Wrong Enrollment Student")
        self.assertNotContains(response, "Foreign Student")
        self.assertNotContains(response, "Foreign Waiver")
        self.assertEqual(response.context["total_students"], 1)
        self.assertEqual(response.context["complete_students"], 1)
        self.assertEqual(len(response.context["student_docs"]), 1)
        submissions = response.context["student_docs"][0]["submissions"]
        self.assertEqual(set(submissions), {local_document.pk})
        self.assertEqual(submissions[local_document.pk].pk, local_submission.pk)
        submission_ids = {submission.pk for submission in submissions.values()}
        self.assertNotIn(foreign_document_submission.pk, submission_ids)
        self.assertNotIn(foreign_student_submission.pk, submission_ids)

    def test_foreign_program_document_matrix_is_not_accessible(self):
        ProgramDocument.objects.create(
            program=self.other_program,
            name="Foreign Program Waiver",
            is_required=True,
        )

        response = self.client.get(
            reverse("program_student_documents", args=[self.other_program.pk])
        )

        self.assertNotEqual(response.status_code, 200)
        self.assertNotContains(response, "Foreign Program Waiver", status_code=302)


class ProgramEmergencyContactsTenantIsolationTests(
    ProgramRelatedRecordTenantIsolationBase
):
    def test_contacts_use_only_local_students_and_guardian_relationships(self):
        student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Local",
            last_name="Student",
        )
        Enrollment.objects.create(
            organization=self.organization,
            program=self.program,
            student=student,
            active=True,
        )

        local_guardians = [
            Adult.objects.create(
                organization=self.organization,
                legal_first_name="Local Guardian",
                last_name=name,
                is_parent=True,
                personal_email=f"{name.lower()}@local.example",
                phone_number="412-555-0101",
            )
            for name in ("One", "Two", "Three")
        ]
        local_relationships = [
            AdultStudentRelationship.objects.create(
                organization=self.organization,
                adult=adult,
                student=student,
                relationship_to_student="guardian",
            )
            for adult in local_guardians
        ]

        foreign_guardians = [
            Adult.objects.create(
                organization=self.other_organization,
                legal_first_name="Foreign Guardian",
                last_name=name,
                is_parent=True,
                personal_email=f"{name.lower()}@foreign.example",
                phone_number="412-555-0199",
            )
            for name in ("Primary", "Secondary", "Additional")
        ]
        foreign_relationships = [
            AdultStudentRelationship.objects.create(
                organization=self.other_organization,
                adult=adult,
                student=student,
                relationship_to_student="guardian",
            )
            for adult in foreign_guardians
        ]

        misowned_adult = Adult.objects.create(
            organization=self.organization,
            legal_first_name="Misowned Relationship",
            last_name="Guardian",
            is_parent=True,
            personal_email="misowned@local.example",
            phone_number="412-555-0188",
        )
        AdultStudentRelationship.objects.create(
            organization=self.other_organization,
            adult=misowned_adult,
            student=student,
            relationship_to_student="guardian",
        )

        student.primary_contact_relationship = foreign_relationships[0]
        student.secondary_contact_relationship = foreign_relationships[1]
        student.save(
            update_fields=[
                "primary_contact_relationship",
                "secondary_contact_relationship",
            ]
        )

        local_student_foreign_enrollment = Student.objects.create(
            organization=self.organization,
            legal_first_name="Wrong Enrollment",
            last_name="Student",
        )
        Enrollment.objects.create(
            organization=self.other_organization,
            program=self.program,
            student=local_student_foreign_enrollment,
            active=True,
        )
        foreign_student = Student.objects.create(
            organization=self.other_organization,
            legal_first_name="Foreign",
            last_name="Student",
        )
        Enrollment.objects.create(
            organization=self.organization,
            program=self.program,
            student=foreign_student,
            active=True,
        )

        response = self.client.get(
            reverse("program_emergency_contacts", args=[self.program.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Local Student")
        self.assertNotContains(response, "Wrong Enrollment Student")
        self.assertNotContains(response, "Foreign Student")
        for guardian in foreign_guardians:
            self.assertNotContains(response, guardian.display_name)
            self.assertNotContains(response, guardian.personal_email)
            self.assertNotContains(response, guardian.phone_number)
        self.assertNotContains(response, misowned_adult.display_name)

        row = response.context["rows"][0]
        self.assertEqual(row["primary"].pk, local_guardians[0].pk)
        self.assertEqual(row["secondary"].pk, local_guardians[1].pk)
        self.assertEqual(
            [contact["adult"].pk for contact in row["others"]],
            [local_guardians[2].pk],
        )


class ProgramDocumentDeleteTenantIsolationTests(
    ProgramRelatedRecordTenantIsolationBase
):
    def test_delete_is_refused_when_document_has_foreign_submissions(self):
        document = ProgramDocument.objects.create(
            program=self.program, name="Local Consent", file=""
        )
        foreign_student = Student.objects.create(
            organization=self.other_organization,
            legal_first_name="Foreign",
            last_name="Student",
        )
        student_submission = StudentDocument.objects.create(
            student=foreign_student,
            program_document=document,
            file="",
        )
        foreign_application = Application.objects.create(
            organization=self.other_organization,
            applicant_type=Application.Type.STUDENT,
            email="foreign@example.test",
            program=self.other_program,
        )
        application_submission = ApplicationDocumentSubmission.objects.create(
            organization=self.other_organization,
            application=foreign_application,
            document=document,
            file="",
        )
        from audit.models import AuditLog

        audit_count = AuditLog.objects.count()

        response = self.client.post(
            reverse("program_document_delete", args=[self.program.pk, document.pk])
        )

        self.assertNotEqual(response.status_code, 200)
        self.assertTrue(ProgramDocument.objects.filter(pk=document.pk).exists())
        self.assertTrue(
            StudentDocument.objects.filter(pk=student_submission.pk).exists()
        )
        self.assertTrue(
            ApplicationDocumentSubmission.objects.filter(
                pk=application_submission.pk
            ).exists()
        )
        self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_delete_still_removes_only_local_document_submissions(self):
        document = ProgramDocument.objects.create(
            program=self.program, name="Local Consent", file=""
        )
        local_student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Local",
            last_name="Student",
        )
        local_submission = StudentDocument.objects.create(
            student=local_student,
            program_document=document,
            file="",
        )

        response = self.client.post(
            reverse("program_document_delete", args=[self.program.pk, document.pk])
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProgramDocument.objects.filter(pk=document.pk).exists())
        self.assertFalse(
            StudentDocument.objects.filter(pk=local_submission.pk).exists()
        )
