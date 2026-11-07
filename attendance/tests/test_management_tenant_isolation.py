from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.models import AttendanceSession, RFIDCard
from organizations.models import Organization, UserOrganizationMembership
from organizations.utils import get_default_organization
from programs.models import Program, Student


class AttendanceManagementTenantIsolationTests(TestCase):
    def setUp(self):
        self.organization = get_default_organization()
        self.foreign_organization = Organization.objects.create(
            name="Attendance Foreign",
            slug="attendance-foreign",
            subdomain="attendance-foreign",
        )
        self.foreign_program = Program.objects.create(
            organization=self.foreign_organization, name="Foreign Program"
        )
        self.foreign_student = Student.objects.create(
            organization=self.foreign_organization,
            legal_first_name="ForeignPrivate",
            last_name="Student",
        )
        self.local_student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Local",
            last_name="Student",
        )
        self.user = get_user_model().objects.create_user(
            username="default-tenant-lead", password="password123"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=self.user,
            organization=self.organization,
            role="LeadMentor",
            is_active=True,
        )
        self.client.force_login(self.user)

    def make_foreign_session(self, visitor_name="Foreign Visitor", check_out=None):
        return AttendanceSession.objects.create(
            organization=self.foreign_organization,
            program=self.foreign_program,
            student=self.foreign_student,
            visitor_name=visitor_name,
            check_in=timezone.now(),
            check_out=check_out,
        )

    def test_who_is_here_excludes_foreign_sessions(self):
        self.make_foreign_session()

        response = self.client.get(reverse("attendance_active"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "ForeignPrivate")
        self.assertNotContains(response, "Foreign Visitor")

    def test_attendance_edit_and_delete_cannot_target_foreign_session(self):
        url = reverse("all_attendance")
        session_to_edit = self.make_foreign_session("Keep this visitor")
        edit_response = self.client.post(
            url,
            {
                "action": "update",
                "session_id": session_to_edit.pk,
                "visitor_name": "Changed by foreign tenant",
            },
        )
        session_to_edit.refresh_from_db()
        self.assertNotEqual(edit_response.status_code, 200)
        self.assertEqual(session_to_edit.visitor_name, "Keep this visitor")

        session_to_delete = self.make_foreign_session("Keep this too")
        delete_response = self.client.post(
            url, {"action": "delete", "session_id": session_to_delete.pk}
        )
        self.assertNotEqual(delete_response.status_code, 200)
        self.assertTrue(
            AttendanceSession.objects.filter(pk=session_to_delete.pk).exists()
        )

    def test_close_session_does_not_close_foreign_record(self):
        session = self.make_foreign_session()
        response = self.client.post(
            reverse("close_attendance_session", args=[session.pk])
        )

        self.assertNotEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertIsNone(session.check_out)

    def test_stale_session_closure_only_updates_current_organization(self):
        session = AttendanceSession.objects.create(
            organization=self.foreign_organization,
            program=self.foreign_program,
            student=self.foreign_student,
            check_in=timezone.now() - timezone.timedelta(days=2),
        )

        self.client.post(reverse("close_stale_attendance_sessions"), {"hours": "1"})

        session.refresh_from_db()
        self.assertIsNone(session.check_out)

    def test_visitor_merge_only_updates_current_organization(self):
        session = AttendanceSession.objects.create(
            organization=self.foreign_organization,
            program=self.foreign_program,
            visitor_name="Private visitor name",
            check_in=timezone.now(),
        )

        response = self.client.post(
            reverse("visitor_management"),
            {
                "action": "merge",
                "selected_names": ["Private visitor name"],
                "target_name": "Renamed visitor",
            },
        )

        self.assertEqual(response.status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.visitor_name, "Private visitor name")

    def test_rfid_management_does_not_show_foreign_cards_or_people(self):
        RFIDCard.objects.create(
            organization=self.foreign_organization,
            uid="FOREIGN-RFID",
            student=self.foreign_student,
        )

        response = self.client.get(reverse("rfid_management"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "FOREIGN-RFID")
        self.assertNotContains(response, "ForeignPrivate")

    def test_rfid_assignment_cannot_reassign_a_foreign_card(self):
        card = RFIDCard.objects.create(
            organization=self.foreign_organization,
            uid="SHARED-FOREIGN-RFID",
            student=self.foreign_student,
        )

        response = self.client.post(
            reverse("rfid_management"),
            {
                "action": "assign",
                "person_type": "student",
                "person_id": self.local_student.pk,
                "uid": card.uid,
            },
        )

        self.assertEqual(response.status_code, 302)
        card.refresh_from_db()
        self.assertEqual(card.student, self.foreign_student)
        self.assertFalse(
            RFIDCard.objects.filter(
                organization=self.organization, uid=card.uid
            ).exists()
        )
