from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.models import AttendanceSession, KioskConfig
from programs.models import Adult, Program, ProgramFeature, RolePermission, Student

from .base import make_client, make_lead_mentor_user, make_program, make_student


class AllAttendanceEntriesTests(TestCase):
    def setUp(self):
        self.lead_mentor_group, _ = Group.objects.get_or_create(name="LeadMentor")
        password = "password123"  # nosec B105
        self.lead_mentor_user = User.objects.create_user(
            username="lead_mentor", password=password
        )
        self.lead_mentor_user.groups.add(self.lead_mentor_group)

        self.mentor_group, _ = Group.objects.get_or_create(name="Mentor")
        self.mentor_user = User.objects.create_user(
            username="mentor", password=password
        )
        self.mentor_user.groups.add(self.mentor_group)

        self.program = make_program()
        self.student = make_student(first_name="Test", last_name="Student")
        self.session = AttendanceSession.objects.create(
            program=self.program, student=self.student, check_in=timezone.now()
        )

    def test_lead_mentor_can_access_all_attendance(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        url = reverse("all_attendance")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "All Program Entries")
        self.assertContains(response, self.student.full_name)

    def test_mentor_cannot_access_all_attendance(self):
        self.client.login(username="mentor", password="password123")  # nosec B106
        url = reverse("all_attendance")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("home"), response.url)

    def test_update_attendance_session(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        url = reverse("all_attendance")
        new_check_in = timezone.now().replace(microsecond=0) - timezone.timedelta(
            hours=1
        )
        local_check_in = timezone.localtime(new_check_in)
        response = self.client.post(
            url,
            {
                "action": "update",
                "session_id": self.session.id,
                "check_in": local_check_in.strftime("%Y-%m-%dT%H:%M"),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.session.refresh_from_db()
        self.assertAlmostEqual(
            self.session.check_in, new_check_in, delta=timezone.timedelta(seconds=60)
        )

    def test_delete_attendance_session(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        url = reverse("all_attendance")
        response = self.client.post(
            url,
            {
                "action": "delete",
                "session_id": self.session.id,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AttendanceSession.objects.filter(id=self.session.id).exists())

    def test_update_program_and_visitor_team(self):
        program2 = make_program("Program 2")

        visitor_session = AttendanceSession.objects.create(
            program=self.program,
            visitor_name="John Doe",
            visitor_team_number=1234,
            check_in=timezone.now(),
        )

        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        url = reverse("all_attendance")

        response = self.client.post(
            url,
            {
                "action": "update",
                "session_id": self.session.id,
                "check_in": timezone.localtime(self.session.check_in).strftime(
                    "%Y-%m-%dT%H:%M"
                ),
                "program_id": program2.id,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.session.refresh_from_db()
        self.assertEqual(self.session.program, program2)

        response = self.client.post(
            url,
            {
                "action": "update",
                "session_id": visitor_session.id,
                "check_in": timezone.localtime(visitor_session.check_in).strftime(
                    "%Y-%m-%dT%H:%M"
                ),
                "program_id": self.program.id,
                "visitor_team_number": 5678,
            },
        )
        self.assertEqual(response.status_code, 302)
        visitor_session.refresh_from_db()
        self.assertEqual(visitor_session.visitor_team_number, 5678)

    def test_sorting_attendance_sessions(self):
        student2 = make_student(first_name="Alpha", last_name="Alpha")
        AttendanceSession.objects.create(
            program=self.program,
            student=student2,
            check_in=timezone.now() - timezone.timedelta(days=1),
        )

        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        url = reverse("all_attendance")

        response = self.client.get(url)
        sessions = list(response.context["sessions"])
        self.assertEqual(sessions[0].student.last_name, "Student")
        self.assertEqual(sessions[1].student.last_name, "Alpha")

        response = self.client.get(f"{url}?sort=person&dir=asc")
        sessions = list(response.context["sessions"])
        self.assertEqual(sessions[0].student.last_name, "Alpha")
        self.assertEqual(sessions[1].student.last_name, "Student")

    def test_all_attendance_page_shows_12_hour_format(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        url = reverse("all_attendance")
        response = self.client.get(url)
        content = response.content.decode()

        import re

        self.assertTrue(re.search(r"\d{1,2}:\d{2}\s+(AM|PM)", content, re.IGNORECASE))


class AttendanceNewViewsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="mentor", password="password"
        )  # nosec B106
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()

        self.program = make_program()
        self.student = make_student(
            first_name="John", last_name="Doe", graduation_year=2026
        )

        self.session = AttendanceSession.objects.create(
            program=self.program, student=self.student, check_in=timezone.now()
        )

        self.client.login(username="mentor", password="password")  # nosec B106

    def test_active_manifest_view(self):
        response = self.client.get(reverse("attendance_manifest"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "John Doe")
        year_str = f"({self.program.start_date.year}-{self.program.end_date.year})"
        self.assertContains(response, f"Test Program {year_str}")

    def test_mentor_access_rfid_management(self):
        mentor_user = User.objects.create_user(
            username="regular_mentor", password="password"
        )  # nosec B106
        mentor = Adult.objects.create(
            user=mentor_user, first_name="Reg", last_name="Mentor", is_mentor=True
        )

        RolePermission.objects.update_or_create(
            role="Mentor",
            section="attendance",
            defaults={"can_read": True, "can_write": True},
        )

        self.client.login(username="regular_mentor", password="password")  # nosec B106

        response = self.client.get(reverse("rfid_management"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RFID Management")
        self.assertContains(response, reverse("rfid_management"))

    def test_all_attendance_view_program_years(self):
        response = self.client.get(reverse("all_attendance"))
        self.assertEqual(response.status_code, 200)
        year_str = f"({self.program.start_date.year}-{self.program.end_date.year})"
        self.assertContains(response, f"Test Program {year_str}")

    def test_active_manifest_filter(self):
        other_program = make_program("Other Program")
        response = self.client.get(
            reverse("attendance_manifest") + f"?program_id={other_program.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "John Doe")

    def test_attendance_summary_view(self):
        self.session.check_out = self.session.check_in + timezone.timedelta(hours=2)
        self.session.recompute_duration()
        self.session.save()

        response = self.client.get(reverse("attendance_summary"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "John Doe")
        self.assertContains(response, "2h 0m")

    def test_rfid_management_view_get(self):
        from attendance.models import RFIDCard

        RFIDCard.objects.create(uid="12345", student=self.student, is_active=True)
        response = self.client.get(reverse("rfid_management"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RFID Management")
        self.assertContains(response, "12345")
        self.assertContains(response, "John Doe")
        self.assertContains(response, "Currently Assigned RFID Cards")

    def test_rfid_management_search(self):
        response = self.client.get(reverse("rfid_management"), {"q": "John"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "John Doe")

    def test_rfid_management_assign_student(self):
        url = reverse("rfid_management") + "?q=John"
        response = self.client.post(
            url,
            {
                "action": "assign",
                "person_type": "student",
                "person_id": self.student.id,
                "uid": "STUDENT-RFID-123",
            },
        )
        self.assertEqual(response.status_code, 302)
        from attendance.models import RFIDCard

        self.assertTrue(
            RFIDCard.objects.filter(
                uid="STUDENT-RFID-123", student=self.student, is_active=True
            ).exists()
        )

    def test_rfid_management_assign_mentor(self):
        mentor = Adult.objects.create(
            first_name="Jane", last_name="Mentor", is_mentor=True
        )
        url = reverse("rfid_management") + "?q=Jane"
        response = self.client.post(
            url,
            {
                "action": "assign",
                "person_type": "mentor",
                "person_id": mentor.id,
                "uid": "MENTOR-RFID-456",
            },
        )
        self.assertEqual(response.status_code, 302)
        from attendance.models import RFIDCard

        self.assertTrue(
            RFIDCard.objects.filter(
                uid="MENTOR-RFID-456", adult=mentor, is_active=True
            ).exists()
        )

    def test_rfid_management_deactivate(self):
        from attendance.models import RFIDCard

        card = RFIDCard.objects.create(uid="OLD-RFID", student=self.student)
        url = reverse("rfid_management") + "?q=John"
        response = self.client.post(url, {"action": "deactivate", "card_id": card.id})
        self.assertEqual(response.status_code, 302)
        card.refresh_from_db()
        self.assertFalse(card.is_active)


class AttendanceImportPermissionTests(TestCase):
    DENIED_MESSAGE = "You do not have permission to import attendance."

    def setUp(self):
        self.lead_mentor_user = make_lead_mentor_user()
        self.mentor_user = User.objects.create_user(
            username="mentor_user", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=self.mentor_user, first_name="Mentor", last_name="User", is_mentor=True
        )
        RolePermission.objects.update_or_create(
            role="Mentor",
            section="attendance",
            defaults={"can_read": True, "can_write": True},
        )

        self.parent_user = User.objects.create_user(
            username="parent_user", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=self.parent_user, first_name="Parent", last_name="User", is_parent=True
        )

        self.student_user = User.objects.create_user(
            username="student_user", password="password123"  # nosec B106
        )
        Student.objects.create(
            user=self.student_user, first_name="Student", last_name="User"
        )

        self.url = reverse("attendance_import")

    def _post(self):
        return self.client.post(self.url, {}, follow=True)

    def _messages(self, response):
        return [str(m) for m in response.context["messages"]]

    def test_lead_mentor_can_post(self):
        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        response = self._post()
        self.assertEqual(response.redirect_chain[-1][0], reverse("import_dashboard"))
        self.assertNotIn(self.DENIED_MESSAGE, self._messages(response))

    def test_mentor_can_post(self):
        self.client.login(username="mentor_user", password="password123")  # nosec B106
        response = self._post()
        self.assertEqual(response.redirect_chain[-1][0], reverse("import_dashboard"))
        self.assertNotIn(self.DENIED_MESSAGE, self._messages(response))

    def test_parent_cannot_post(self):
        self.client.login(username="parent_user", password="password123")  # nosec B106
        response = self._post()
        self.assertIn(self.DENIED_MESSAGE, self._messages(response))

    def test_student_cannot_post(self):
        self.client.login(username="student_user", password="password123")  # nosec B106
        response = self._post()
        self.assertIn(self.DENIED_MESSAGE, self._messages(response))


class StudentAttendanceObjectPermissionTests(TestCase):
    def setUp(self):
        self.parent_user = User.objects.create_user(
            username="parent_user2", password="password123"  # nosec B106
        )
        self.parent = Adult.objects.create(
            user=self.parent_user,
            first_name="Parent",
            last_name="Two",
            is_parent=True,
        )

        self.child = make_student(first_name="My", last_name="Child")
        self.parent.students.add(self.child)

        self.other_student = make_student(first_name="Other", last_name="Student")

    def test_parent_can_view_own_child_attendance(self):
        self.client.login(username="parent_user2", password="password123")  # nosec B106
        url = reverse("student_attendance", args=[self.child.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_parent_cannot_view_other_student_attendance(self):
        self.client.login(username="parent_user2", password="password123")  # nosec B106
        url = reverse("student_attendance", args=[self.other_student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))


class StudentAttendanceRoleTests(TestCase):
    def setUp(self):
        self.mentor_user = User.objects.create_user(
            username="mentor_user3", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=self.mentor_user, first_name="Mentor", last_name="User", is_mentor=True
        )

        self.role_perm = RolePermission.objects.update_or_create(
            role="Mentor",
            section="attendance",
            defaults={"can_read": True, "can_write": True},
        )[0]

        self.student_user1 = User.objects.create_user(
            username="student_user1", password="password123"  # nosec B106
        )
        self.student1 = make_student(
            user=self.student_user1, first_name="Student", last_name="One"
        )

        self.student_user2 = User.objects.create_user(
            username="student_user2", password="password123"  # nosec B106
        )
        self.student2 = make_student(
            user=self.student_user2, first_name="Student", last_name="Two"
        )

    def test_mentor_can_view_attendance_by_default(self):
        self.client.login(username="mentor_user3", password="password123")  # nosec B106
        url = reverse("student_attendance", args=[self.student1.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_mentor_cannot_view_attendance_when_denied(self):
        RolePermission.objects.update_or_create(
            role="Mentor",
            section="attendance",
            defaults={"can_read": False, "can_write": False},
        )
        self.client.login(username="mentor_user3", password="password123")  # nosec B106
        url = reverse("student_attendance", args=[self.student1.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

    def test_student_can_view_own_attendance(self):
        self.client.login(
            username="student_user1", password="password123"
        )  # nosec B106
        url = reverse("student_attendance", args=[self.student1.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_student_cannot_view_other_attendance(self):
        self.client.login(
            username="student_user1", password="password123"
        )  # nosec B106
        url = reverse("student_attendance", args=[self.student2.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))


class MentorAttendanceDeleteViewTests(TestCase):
    def setUp(self):
        self.program = make_program()
        self.mentor_user = User.objects.create_user(
            username="mentor_delete", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=self.mentor_user,
            first_name="Mentor",
            last_name="Deleter",
            is_mentor=True,
        )

        self.student = make_student(first_name="Del", last_name="Student")

        self.session = AttendanceSession.objects.create(
            program=self.program,
            student=self.student,
            check_in=timezone.now(),
        )

        RolePermission.objects.update_or_create(
            role="Mentor",
            section="attendance",
            defaults={"can_read": True, "can_write": True},
        )

    def test_mentor_cannot_delete_session_via_view(self):
        self.client.login(
            username="mentor_delete", password="password123"
        )  # nosec B106
        url = reverse("student_attendance", args=[self.student.pk])
        response = self.client.post(
            url,
            {
                "action": "delete",
                "session_id": self.session.pk,
            },
        )
        self.assertTrue(AttendanceSession.objects.filter(pk=self.session.pk).exists())
        self.assertIn(response.status_code, [302, 403])
