from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from programs.models import Adult, RolePermission, Student


class AttendanceImportPermissionTests(TestCase):
    """TDD for Issue 6: AttendanceImportView should use the dynamic permission
    system (can_user_write) instead of the legacy Django model permission
    ``programs.change_student``.
    """

    DENIED_MESSAGE = "You do not have permission to import attendance."

    def setUp(self):
        # LeadMentor
        self.lead_mentor_user = User.objects.create_user(
            username="lead_mentor", password="password123"  # nosec B106
        )
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor_user.groups.add(lm_group)

        # Mentor with explicit write access to the attendance section
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

        # Parent
        self.parent_user = User.objects.create_user(
            username="parent_user", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=self.parent_user, first_name="Parent", last_name="User", is_parent=True
        )

        # Student
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
        # No file/program supplied, so we expect a validation error (proof
        # that permission was granted and the view logic ran), never the
        # login page or the permission-denied message.
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
    """TDD for Issue 7: student_attendance_view should perform a single
    object-aware ``can_user_read(user, "attendance", obj=student)`` check
    instead of a redundant, manually re-implemented Parent check.
    """

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

        self.child = Student.objects.create(first_name="My", last_name="Child")
        self.parent.students.add(self.child)

        self.other_student = Student.objects.create(
            first_name="Other", last_name="Student"
        )

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
        # Mentor
        self.mentor_user = User.objects.create_user(
            username="mentor_user3", password="password123"  # nosec B106
        )
        Adult.objects.create(
            user=self.mentor_user, first_name="Mentor", last_name="User", is_mentor=True
        )

        # Student 1
        self.student_user1 = User.objects.create_user(
            username="student_user1", password="password123"  # nosec B106
        )
        self.student1 = Student.objects.create(
            user=self.student_user1, first_name="Student", last_name="One"
        )

        # Student 2
        self.student_user2 = User.objects.create_user(
            username="student_user2", password="password123"  # nosec B106
        )
        self.student2 = Student.objects.create(
            user=self.student_user2, first_name="Student", last_name="Two"
        )

    def test_mentor_cannot_view_attendance(self):
        self.client.login(username="mentor_user3", password="password123")  # nosec B106
        url = reverse("student_attendance", args=[self.student1.pk])
        response = self.client.get(url)
        # Should be redirected to home
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
