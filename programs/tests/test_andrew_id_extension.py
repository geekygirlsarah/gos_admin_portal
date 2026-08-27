from django.contrib.auth.models import Group, User
from django.db.models import Value
from django.db.models.functions import Coalesce, Lower
from django.test import TestCase
from django.urls import reverse

from programs.forms import AdultForm, StudentForm
from programs.models import Adult, Student
from programs.views.andrew_ids import _get_adult_sponsor_choices


class AndrewIdExtensionTest(TestCase):
    def setUp(self):
        # Create LeadMentor group
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_user = User.objects.create_user(
            username="leadmentor", password="password"
        )  # nosec B106
        self.lead_user.groups.add(self.lead_group)

        # Create some adults
        # Mentors
        self.mentor1 = Adult.objects.create(
            legal_first_name="Zebra", last_name="Alpha", is_mentor=True
        )
        self.mentor2 = Adult.objects.create(
            legal_first_name="Alice",
            preferred_first_name="Betty",
            last_name="Gamma",
            is_mentor=True,
        )
        self.mentor3 = Adult.objects.create(
            legal_first_name="Charlie", last_name="Delta", is_mentor=True
        )

        # Non-mentors
        self.parent = Adult.objects.create(
            legal_first_name="Parent", last_name="User", is_parent=True, is_mentor=False
        )

        # Student
        self.student = Student.objects.create(legal_first_name="Stu", last_name="Dent")

    def test_student_form_andrew_id_sponsor_queryset(self):
        form = StudentForm()
        queryset = form.fields["andrew_id_sponsor"].queryset

        # Filter by mentors only
        mentor_ids = [self.mentor1.id, self.mentor2.id, self.mentor3.id]
        queryset_ids = list(queryset.values_list("id", flat=True))

        for m_id in mentor_ids:
            self.assertIn(m_id, queryset_ids)
        self.assertNotIn(self.parent.id, queryset_ids)

        # Sorting: Betty, Charlie, Zebra
        expected_order = [self.mentor2.id, self.mentor3.id, self.mentor1.id]
        actual_order = list(queryset.values_list("id", flat=True))
        self.assertEqual(actual_order, expected_order)

    def test_get_adult_sponsor_choices(self):
        queryset = _get_adult_sponsor_choices()

        # Filter by mentors only
        mentor_ids = [self.mentor1.id, self.mentor2.id, self.mentor3.id]
        queryset_ids = list(queryset.values_list("id", flat=True))

        for m_id in mentor_ids:
            self.assertIn(m_id, queryset_ids)
        self.assertNotIn(self.parent.id, queryset_ids)

        # Sorting
        expected_order = [self.mentor2.id, self.mentor3.id, self.mentor1.id]
        actual_order = list(queryset.values_list("id", flat=True))
        self.assertEqual(actual_order, expected_order)

    def test_andrew_id_management_view_student_save(self):
        self.client.login(username="leadmentor", password="password")  # nosec B106
        url = reverse("andrew_id_management")

        # Save Andrew ID, expiration and sponsor for student
        response = self.client.post(
            url,
            {
                "action": "set",
                "person_type": "student",
                "person_id": self.student.id,
                "andrew_id": "stud123",
                "andrew_id_expiration": "2026-12-31",
                "andrew_id_sponsor": self.mentor2.id,
            },
        )

        self.student.refresh_from_db()
        self.assertEqual(self.student.andrew_id, "stud123")
        self.assertEqual(str(self.student.andrew_id_expiration), "2026-12-31")
        self.assertEqual(self.student.andrew_id_sponsor, self.mentor2)

    def test_andrew_id_management_view_student_clear(self):
        self.student.andrew_id = "stud123"
        self.student.andrew_id_expiration = "2026-12-31"
        self.student.andrew_id_sponsor = self.mentor2
        self.student.save()

        self.client.login(username="leadmentor", password="password")  # nosec B106
        url = reverse("andrew_id_management")

        # Clear Andrew ID for student
        response = self.client.post(
            url,
            {"action": "clear", "person_type": "student", "person_id": self.student.id},
        )

        self.student.refresh_from_db()
        self.assertIsNone(self.student.andrew_id)
        self.assertIsNone(self.student.andrew_id_expiration)
        self.assertIsNone(self.student.andrew_id_sponsor)
