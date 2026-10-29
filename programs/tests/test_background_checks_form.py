import datetime

from django.test import TestCase
from django.utils import timezone

from programs.forms import BackgroundChecksForm
from programs.models import Adult, BackgroundCheck, BackgroundCheckType, Student


class BackgroundChecksFormTests(TestCase):
    def setUp(self):
        self.bct_fbi = BackgroundCheckType.objects.create(
            name="FBI Fingerprint (Act 114)",
            validity_years=5,
            applies_to_students=True,
            student_min_age=17,
        )
        self.bct_sp = BackgroundCheckType.objects.create(
            name="PA State Police Criminal History (PATCH)",
            validity_years=5,
            applies_to_students=True,
            student_min_age=17,
        )
        self.bct_ca = BackgroundCheckType.objects.create(
            name="PA Child Abuse History (Act 151)",
            validity_years=5,
            applies_to_students=True,
            student_min_age=17,
        )

    def test_fields_exist_for_each_check_type(self):
        form = BackgroundChecksForm()
        for bct in [self.bct_fbi, self.bct_sp, self.bct_ca]:
            self.assertIn(f"cleared_{bct.id}", form.fields)
            self.assertIn(f"obtained_{bct.id}", form.fields)

    def test_save_creates_rows_for_cleared_types(self):
        student = Student.objects.create(legal_first_name="A", last_name="B")
        form = BackgroundChecksForm(
            data={
                f"cleared_{self.bct_sp.id}": "on",
                f"obtained_{self.bct_sp.id}": "2021-01-01",
                f"cleared_{self.bct_ca.id}": "on",
                f"obtained_{self.bct_ca.id}": "2021-06-01",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save(student=student)
        checks = student.background_checks.all()
        self.assertEqual(checks.count(), 2)
        for check in checks:
            self.assertTrue(check.cleared)
        sp = checks.get(check_type=self.bct_sp)
        self.assertEqual(sp.obtained_date, datetime.date(2021, 1, 1))

    def test_save_removes_rows_when_cleared_is_false(self):
        student = Student.objects.create(legal_first_name="A", last_name="B")
        existing = BackgroundCheck.objects.create(
            student=student,
            check_type=self.bct_fbi,
            cleared=True,
            obtained_date=datetime.date(2020, 1, 1),
        )
        form = BackgroundChecksForm(data={f"obtained_{self.bct_fbi.id}": ""})
        self.assertTrue(form.is_valid(), form.errors)
        form.save(student=student)
        self.assertFalse(student.background_checks.filter(pk=existing.pk).exists())

    def test_save_creates_rows_for_adult(self):
        adult = Adult.objects.create(legal_first_name="C", last_name="D")
        form = BackgroundChecksForm(
            data={
                f"cleared_{self.bct_fbi.id}": "on",
                f"obtained_{self.bct_fbi.id}": "2022-03-03",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save(adult=adult)
        self.assertEqual(adult.background_checks.count(), 1)
        self.assertTrue(adult.background_checks.get(check_type=self.bct_fbi).cleared)

    def test_requires_exactly_one_holder(self):
        student = Student.objects.create(legal_first_name="A", last_name="B")
        adult = Adult.objects.create(legal_first_name="C", last_name="D")
        with self.assertRaises(ValueError):
            BackgroundChecksForm(data={}).save()
        with self.assertRaises(ValueError):
            BackgroundChecksForm(data={}).save(student=student, adult=adult)


class BackgroundChecksViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group, User

        self.bct_fbi = BackgroundCheckType.objects.create(
            name="FBI Fingerprint (Act 114)",
            validity_years=5,
            applies_to_students=True,
            student_min_age=17,
        )
        self.bct_sp = BackgroundCheckType.objects.create(
            name="PA State Police Criminal History (PATCH)",
            validity_years=5,
            applies_to_students=True,
            student_min_age=17,
        )

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
                f"cleared_{self.bct_fbi.id}": "on",
                f"obtained_{self.bct_fbi.id}": "2021-01-01",
                f"cleared_{self.bct_sp.id}": "on",
                f"obtained_{self.bct_sp.id}": "2021-02-01",
            },
        )
        self.assertIn(resp.status_code, [200, 302])
        checks = student.background_checks.all()
        self.assertEqual(checks.count(), 2)
        self.assertTrue(checks.get(check_type=self.bct_fbi).cleared)

    def test_adult_edit_saves_background_checks_for_lead_mentor(self):
        from django.urls import reverse

        adult = Adult.objects.create(legal_first_name="C", last_name="D")
        url = reverse("adult_edit", args=[adult.pk])
        resp = self.client.post(
            url,
            {
                "legal_first_name": "C",
                "last_name": "D",
                f"cleared_{self.bct_fbi.id}": "on",
                f"obtained_{self.bct_fbi.id}": "2021-03-01",
            },
        )
        self.assertIn(resp.status_code, [200, 302])
        self.assertEqual(adult.background_checks.count(), 1)
        self.assertTrue(adult.background_checks.get(check_type=self.bct_fbi).cleared)


class BackgroundCheckStatusTagTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        self.bct_fbi = BackgroundCheckType.objects.create(
            name="FBI Fingerprint (Act 114)",
            validity_years=5,
            applies_to_students=True,
            student_min_age=17,
        )
        self.bct_sp = BackgroundCheckType.objects.create(
            name="PA State Police Criminal History (PATCH)",
            validity_years=5,
            applies_to_students=True,
            student_min_age=17,
        )

        self.student_user = User.objects.create_user(
            username="student", password="pass12345"  # nosec B106
        )
        self.student = Student.objects.create(
            user=self.student_user,
            legal_first_name="A",
            last_name="B",
            date_of_birth=datetime.date(2000, 1, 1),
        )

    def test_status_lists_all_check_types(self):
        from django.template import Context, Template

        tpl = Template(
            "{% load form_tags %}{% for s in student|background_check_status %}"
            "{{ s.check_type }}:{{ s.on_file }};{% endfor %}"
        )
        html = tpl.render(Context({"student": self.student}))
        self.assertIn("FBI Fingerprint (Act 114):False;", html)
        self.assertIn("PA State Police Criminal History (PATCH):False;", html)

    def test_status_reflects_on_file_valid_check(self):
        from django.template import Context, Template

        BackgroundCheck.objects.create(
            student=self.student,
            check_type=self.bct_fbi,
            cleared=True,
            obtained_date=timezone.localdate() - datetime.timedelta(days=30),
        )
        tpl = Template(
            "{% load form_tags %}"
            "{% for s in student|background_check_status %}"
            "{{ s.check_type }}:{{ s.on_file }}:{{ s.is_valid }};"
            "{% endfor %}"
        )
        html = tpl.render(Context({"student": self.student}))
        self.assertIn("FBI Fingerprint (Act 114):True:True;", html)
        self.assertIn("PA State Police Criminal History (PATCH):False:False;", html)


class StudentDetailBackgroundCheckViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        from programs.models import Enrollment, Program, ProgramFeature

        self.bct_fbi = BackgroundCheckType.objects.create(
            name="FBI Fingerprint (Act 114)",
            validity_years=5,
            applies_to_students=True,
            student_min_age=17,
        )
        self.bct_sp = BackgroundCheckType.objects.create(
            name="PA State Police Criminal History (PATCH)",
            validity_years=5,
            applies_to_students=True,
            student_min_age=17,
        )
        self.bct_ca = BackgroundCheckType.objects.create(
            name="PA Child Abuse History (Act 151)",
            validity_years=5,
            applies_to_students=True,
            student_min_age=17,
        )

        self.student_user = User.objects.create_user(
            username="student", password="pass12345"  # nosec B106
        )
        self.student = Student.objects.create(
            user=self.student_user,
            legal_first_name="A",
            last_name="B",
            date_of_birth=datetime.date(2000, 1, 1),
        )
        feature, _ = ProgramFeature.objects.get_or_create(
            key="background-checks", defaults={"name": "Background Checks"}
        )
        self.program = Program.objects.create(name="Test Program")
        self.program.features.add(feature)
        Enrollment.objects.create(student=self.student, program=self.program)
        self.client.login(username="student", password="pass12345")  # nosec B106

    def test_not_required_shows_not_needed_message(self):
        from django.urls import reverse

        self.student.date_of_birth = timezone.localdate() - datetime.timedelta(
            days=12 * 365
        )
        self.student.save()
        url = reverse("student_detail", args=[self.student.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Not needed for your age")

    def test_required_shows_all_three_checks_with_status(self):
        from django.urls import reverse

        url = reverse("student_detail", args=[self.student.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "FBI Fingerprint (Act 114)")
        self.assertContains(resp, "PA State Police Criminal History (PATCH)")
        self.assertContains(resp, "PA Child Abuse History (Act 151)")
        self.assertContains(resp, "Missing")
        self.assertNotContains(resp, "Not needed for your age")
