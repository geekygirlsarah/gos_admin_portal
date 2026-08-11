import datetime

from django.test import TestCase

from programs.forms import BackgroundChecksForm
from programs.models import (
    Adult,
    BackgroundCheck,
    BackgroundCheckType,
    Student,
)


class BackgroundChecksFormTests(TestCase):
    def test_fields_exist_for_each_check_type(self):
        form = BackgroundChecksForm()
        for check_type in BackgroundCheckType.values:
            self.assertIn(f"cleared_{check_type}", form.fields)
            self.assertIn(f"obtained_{check_type}", form.fields)

    def test_save_creates_rows_for_cleared_types(self):
        student = Student.objects.create(legal_first_name="A", last_name="B")
        form = BackgroundChecksForm(
            data={
                "cleared_state_police": "on",
                "obtained_state_police": "2021-01-01",
                "cleared_child_abuse": "on",
                "obtained_child_abuse": "2021-06-01",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save(student=student)
        checks = student.background_checks.all()
        self.assertEqual(checks.count(), 2)
        for check in checks:
            self.assertTrue(check.cleared)
        sp = checks.get(check_type=BackgroundCheckType.STATE_POLICE)
        self.assertEqual(sp.obtained_date, datetime.date(2021, 1, 1))

    def test_save_removes_rows_when_cleared_is_false(self):
        student = Student.objects.create(legal_first_name="A", last_name="B")
        existing = BackgroundCheck.objects.create(
            student=student,
            check_type=BackgroundCheckType.FBI,
            cleared=True,
            obtained_date=datetime.date(2020, 1, 1),
        )
        form = BackgroundChecksForm(data={"obtained_fbi": ""})
        self.assertTrue(form.is_valid(), form.errors)
        form.save(student=student)
        self.assertFalse(student.background_checks.filter(pk=existing.pk).exists())

    def test_save_creates_rows_for_adult(self):
        adult = Adult.objects.create(first_name="C", last_name="D")
        form = BackgroundChecksForm(
            data={
                "cleared_fbi": "on",
                "obtained_fbi": "2022-03-03",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save(adult=adult)
        self.assertEqual(adult.background_checks.count(), 1)
        self.assertTrue(
            adult.background_checks.get(check_type=BackgroundCheckType.FBI).cleared
        )

    def test_requires_exactly_one_holder(self):
        student = Student.objects.create(legal_first_name="A", last_name="B")
        adult = Adult.objects.create(first_name="C", last_name="D")
        with self.assertRaises(ValueError):
            BackgroundChecksForm(data={}).save()
        with self.assertRaises(ValueError):
            BackgroundChecksForm(data={}).save(student=student, adult=adult)


class BackgroundChecksViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group, User

        self.user = User.objects.create_user(
            username="lead", password="pass12345"  # nosec B106
        )
        self.group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(self.group)
        self.client.login(username="lead", password="pass12345")  # nosec B106

    def test_student_edit_saves_background_checks_for_lead_mentor(self):
        from django.urls import reverse

        student = Student.objects.create(
            legal_first_name="A",
            last_name="B",
            date_of_birth=datetime.date(2008, 1, 1),
        )
        url = reverse("student_edit", args=[student.pk])
        resp = self.client.post(
            url,
            {
                "legal_first_name": "A",
                "last_name": "B",
                "date_of_birth": "2008-01-01",
                "cleared_fbi": "on",
                "obtained_fbi": "2021-01-01",
                "cleared_state_police": "on",
                "obtained_state_police": "2021-02-01",
            },
        )
        self.assertIn(resp.status_code, [200, 302])
        checks = student.background_checks.all()
        self.assertEqual(checks.count(), 2)
        self.assertTrue(checks.get(check_type="fbi").cleared)

    def test_adult_edit_saves_background_checks_for_lead_mentor(self):
        from django.urls import reverse

        adult = Adult.objects.create(first_name="C", last_name="D")
        url = reverse("adult_edit", args=[adult.pk])
        resp = self.client.post(
            url,
            {
                "first_name": "C",
                "last_name": "D",
                "cleared_fbi": "on",
                "obtained_fbi": "2021-03-01",
            },
        )
        self.assertIn(resp.status_code, [200, 302])
        self.assertEqual(adult.background_checks.count(), 1)
        self.assertTrue(adult.background_checks.get(check_type="fbi").cleared)
