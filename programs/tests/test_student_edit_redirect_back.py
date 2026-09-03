from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse

from programs.models import Program, Student


class StudentEditRedirectBackTests(TestCase):
    """Editing a student should return the user to the screen they came from
    (All Students or Program detail), not the student detail page, and must
    never honor unsafe/user-supplied external URLs.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="staff", password="pass12345"  # nosec B106
        )
        perm = Permission.objects.get(codename="change_student")
        self.user.user_permissions.add(perm)
        group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(group)
        self.client.login(username="staff", password="pass12345")  # nosec B106
        self.student = Student.objects.create(
            legal_first_name="Jane",
            last_name="Smith",
            date_of_birth="2008-05-15",
        )

    def _valid_data(self):
        return {
            "legal_first_name": "Jane",
            "last_name": "Smith",
            "date_of_birth": "2008-05-15",
        }

    def test_edit_from_all_students_returns_to_student_list(self):
        edit_url = "%s?next=%s" % (
            reverse("student_edit", args=[self.student.pk]),
            reverse("student_list"),
        )
        resp = self.client.post(edit_url, self._valid_data())
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("student_list"))

    def test_edit_from_program_returns_to_program_detail(self):
        program = Program.objects.create(
            name="Test Program", active=True, start_date="2026-01-01"
        )
        program_url = reverse("program_detail", args=[program.pk])
        edit_url = "%s?next=%s" % (
            reverse("student_edit", args=[self.student.pk]),
            program_url,
        )
        resp = self.client.post(edit_url, self._valid_data())
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, program_url)

    def test_edit_without_next_returns_to_student_detail(self):
        edit_url = reverse("student_edit", args=[self.student.pk])
        resp = self.client.post(edit_url, self._valid_data())
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("student_detail", args=[self.student.pk]))

    def test_edit_rejects_external_next_url(self):
        """An external/user-supplied 'next' must never be honored (bandit-safe)."""
        external = "https://evil.example/phish"
        edit_url = "%s?next=%s" % (
            reverse("student_edit", args=[self.student.pk]),
            external,
        )
        resp = self.client.post(edit_url, self._valid_data())
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("student_detail", args=[self.student.pk]))

    def test_student_detail_edit_button_forwards_incoming_next(self):
        """The Edit button on student detail must forward the incoming 'next'
        (the All Students or Program screen the user came from) rather than
        overwriting it with the detail page path.
        """
        program = Program.objects.create(
            name="Test Program", active=True, start_date="2026-01-01"
        )
        program_url = reverse("program_detail", args=[program.pk])
        detail_url = "%s?next=%s" % (
            reverse("student_detail", args=[self.student.pk]),
            program_url,
        )
        resp = self.client.get(detail_url)
        self.assertEqual(resp.status_code, 200)
        edit_url = reverse("student_edit", args=[self.student.pk])
        expected = "%s?next=%s" % (edit_url, program_url)
        self.assertContains(resp, expected)

        # And the student_list-forwarding case
        list_url = reverse("student_list")
        detail_url2 = "%s?next=%s" % (
            reverse("student_detail", args=[self.student.pk]),
            list_url,
        )
        resp2 = self.client.get(detail_url2)
        expected2 = "%s?next=%s" % (edit_url, list_url)
        self.assertContains(resp2, expected2)
