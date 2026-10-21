import csv
from datetime import date
from decimal import Decimal
from io import BytesIO, StringIO

from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from programs.models import Adult, Enrollment, Fee, Payment, Program, Student


class ProgramStudentExportViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mentor_user", password="pass12345"  # nosec B106
        )
        group, _ = Group.objects.get_or_create(name="Mentor")
        self.user.groups.add(group)
        self.perm = Permission.objects.get(codename="view_student")
        self.user.user_permissions.add(self.perm)
        self.client.login(username="mentor_user", password="pass12345")  # nosec B106

        self.program = Program.objects.create(name="Robotics", active=True)

        self.student_a = Student.objects.create(
            legal_first_name="Alice", last_name="Smith", graduation_year=2029
        )
        self.student_b = Student.objects.create(
            legal_first_name="Bob", last_name="Jones", graduation_year=2031
        )
        self.student_c = Student.objects.create(
            legal_first_name="Charlie", last_name="Adams"
        )
        self.student_graduated = Student.objects.create(
            legal_first_name="Diana", last_name="Prince", graduated=True
        )

        Enrollment.objects.create(
            student=self.student_a, program=self.program, active=True
        )
        Enrollment.objects.create(
            student=self.student_b, program=self.program, active=True
        )
        Enrollment.objects.create(
            student=self.student_c, program=self.program, active=False
        )
        Enrollment.objects.create(
            student=self.student_graduated, program=self.program, active=True
        )

    def test_export_requires_login(self):
        self.client.logout()
        url = reverse("program_student_export", args=[self.program.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_export_returns_xlsx(self):
        url = reverse("program_student_export", args=[self.program.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn(".xlsx", resp["Content-Disposition"])

    def test_mentor_export_includes_only_active_enrolled_students(self):
        url = reverse("program_student_export", args=[self.program.pk])
        resp = self.client.get(url)
        wb = load_workbook(BytesIO(resp.content))
        ws = wb.active

        headers = [cell.value for cell in ws[1]]
        self.assertEqual(headers, ["First Name", "Last Name", "Grade"])

        names = [(row[0].value, row[1].value) for row in ws.iter_rows(min_row=2)]
        first_names = {n[0] for n in names}
        self.assertIn("Alice", first_names)
        self.assertIn("Bob", first_names)
        self.assertNotIn("Charlie", first_names)
        self.assertNotIn("Diana", first_names)

    def test_export_includes_grade(self):
        url = reverse("program_student_export", args=[self.program.pk])
        resp = self.client.get(url)
        wb = load_workbook(BytesIO(resp.content))
        ws = wb.active

        rows = {row[0].value: row[2].value for row in ws.iter_rows(min_row=2)}
        # Alice: grad 2029 → should have a grade
        self.assertIsNotNone(rows["Alice"])
        self.assertNotEqual(rows["Alice"], "")
        # Charlie and Diana: inactive/graduated → not in rows
        self.assertNotIn("Charlie", rows)
        self.assertNotIn("Diana", rows)

    def test_export_students_sorted_alphabetically(self):
        url = reverse("program_student_export", args=[self.program.pk])
        resp = self.client.get(url)
        wb = load_workbook(BytesIO(resp.content))
        ws = wb.active

        first_names = [row[0].value for row in ws.iter_rows(min_row=2)]
        self.assertEqual(first_names, sorted(first_names))

    def test_export_filename_contains_program_name(self):
        url = reverse("program_student_export", args=[self.program.pk])
        resp = self.client.get(url)
        self.assertIn("Robotics", resp["Content-Disposition"])

    def test_404_for_nonexistent_program(self):
        url = reverse("program_student_export", args=[9999])
        resp = self.client.get(url)
        self.assertIn(resp.status_code, (302, 404))


class ProgramStudentExportExtendedTests(TestCase):
    """Tests for the optional Lead Mentor export field groups and CSV format."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="leadmentor", password="pass12345"  # nosec B106
        )
        group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(group)
        self.client.login(username="leadmentor", password="pass12345")  # nosec B106

        self.program = Program.objects.create(name="Robotics", active=True)

        self.student_a = Student.objects.create(
            legal_first_name="Alice",
            last_name="Smith",
            graduation_year=2029,
            personal_email="alice@example.com",
            phone_number="555-1234",
        )
        Enrollment.objects.create(
            student=self.student_a, program=self.program, active=True
        )

        self.parent_one = Adult.objects.create(
            legal_first_name="Mary",
            last_name="Smith",
            personal_email="mary@example.com",
            phone_number="555-0001",
            email_updates=True,
        )
        self.parent_two = Adult.objects.create(
            legal_first_name="John",
            preferred_first_name="Johnny",
            last_name="Smith",
            personal_email="john@example.com",
            phone_number="555-0002",
        )
        self.student_a.primary_contact = self.parent_one
        self.student_a.secondary_contact = self.parent_two
        self.student_a.save()

        # Customize the primary relationship so its labels are exercised.
        self.student_a.primary_contact_relationship.specific_relationship = "mother"
        self.student_a.primary_contact_relationship.save()

    def _url(self, query=""):
        base = reverse("program_student_export", args=[self.program.pk])
        return f"{base}?{query}" if query else base

    def _sheet(self, resp):
        return load_workbook(BytesIO(resp.content)).active

    def test_default_lead_export_has_active_column_and_all_students(self):
        # Create an inactive student in the program
        student_inactive = Student.objects.create(
            legal_first_name="Bob",
            last_name="Inactive",
            graduation_year=2030,
        )
        Enrollment.objects.create(
            student=student_inactive, program=self.program, active=False
        )

        ws = self._sheet(self.client.get(self._url()))
        headers = [cell.value for cell in ws[1]]
        self.assertEqual(headers, ["First Name", "Last Name", "Grade", "Active"])

        rows = {row[0].value: row[3].value for row in ws.iter_rows(min_row=2)}
        self.assertEqual(rows["Alice"], "Yes")
        self.assertEqual(rows["Bob"], "No")

    def test_contact_group_adds_student_email_and_school(self):
        ws = self._sheet(self.client.get(self._url("groups=contact")))
        headers = [cell.value for cell in ws[1]]
        self.assertEqual(
            headers,
            [
                "First Name",
                "Last Name",
                "Grade",
                "Active",
                "Student Email",
                "Student Phone",
                "School",
                "Graduation Year",
            ],
        )
        row = {h: cell.value for h, cell in zip(headers, ws[2])}
        self.assertEqual(row["Student Email"], "alice@example.com")
        self.assertEqual(row["Student Phone"], "555-1234")
        self.assertEqual(row["Graduation Year"], 2029)
        self.assertEqual(row["Active"], "Yes")

    def test_parents_group_adds_parent_contacts(self):
        ws = self._sheet(self.client.get(self._url("groups=parents")))
        headers = [cell.value for cell in ws[1]]
        self.assertIn("Parent 1 First Name", headers)
        self.assertIn("Parent 1 Last Name", headers)
        self.assertIn("Parent 2 Email", headers)
        row = {h: cell.value for h, cell in zip(headers, ws[2])}
        self.assertEqual(row["Parent 1 First Name"], "Mary")
        self.assertEqual(row["Parent 1 Last Name"], "Smith")
        self.assertEqual(row["Parent 1 Relationship"], "Parent")
        self.assertEqual(row["Parent 1 Specific Relationship"], "mother")
        self.assertEqual(row["Parent 1 Email"], "mary@example.com")
        self.assertEqual(row["Parent 1 Phone"], "555-0001")
        self.assertEqual(row["Parent 1 Email Updates"], "Yes")
        self.assertEqual(row["Parent 2 First Name"], "Johnny")
        self.assertEqual(row["Parent 2 Last Name"], "Smith")
        self.assertEqual(row["Parent 2 Relationship"], "Parent")
        self.assertEqual(row["Parent 2 Email"], "john@example.com")
        self.assertEqual(row["Parent 2 Email Updates"], "No")

    def test_balance_group_adds_balance_columns(self):
        Fee.objects.create(
            program=self.program, name="Tuition", amount=Decimal("100.00")
        )
        Payment.objects.create(
            student=self.student_a,
            program=self.program,
            amount=Decimal("40.00"),
            paid_on=date.today(),
        )
        ws = self._sheet(self.client.get(self._url("groups=balance")))
        headers = [cell.value for cell in ws[1]]
        self.assertEqual(
            headers[-4:],
            ["Total Fees", "Discount", "Payments", "Balance Due"],
        )
        row = {h: cell.value for h, cell in zip(headers, ws[2])}
        self.assertEqual(row["Balance Due"], 60)
        self.assertEqual(row["Payments"], 40)

    def test_multiple_groups_combine_in_order(self):
        ws = self._sheet(self.client.get(self._url("groups=balance&groups=contact")))
        headers = [cell.value for cell in ws[1]]
        self.assertEqual(headers[3], "Active")
        self.assertEqual(
            headers[4:8],
            [
                "Student Email",
                "Student Phone",
                "School",
                "Graduation Year",
            ],
        )
        self.assertEqual(
            headers[-4:],
            [
                "Total Fees",
                "Discount",
                "Payments",
                "Balance Due",
            ],
        )

    def test_csv_format_returns_csv(self):
        resp = self.client.get(self._url("format=csv&groups=contact"))
        self.assertEqual(resp["Content-Type"], "text/csv")
        self.assertIn(".csv", resp["Content-Disposition"])
        rows = list(csv.reader(StringIO(resp.content.decode("utf-8"))))
        self.assertEqual(rows[0][:4], ["First Name", "Last Name", "Grade", "Active"])
        self.assertEqual(rows[1][0], "Alice")
        self.assertEqual(rows[1][3], "Yes")
        self.assertIn("Student Email", rows[0])

    def test_extra_groups_ignored_for_non_lead_mentor(self):
        mentor = User.objects.create_user(
            username="mentor", password="pass12345"  # nosec B106
        )
        mentor_group, _ = Group.objects.get_or_create(name="Mentor")
        mentor.groups.add(mentor_group)
        self.client.login(username="mentor", password="pass12345")  # nosec B106

        ws = self._sheet(self.client.get(self._url("groups=contact,parents,balance")))
        headers = [cell.value for cell in ws[1]]
        self.assertEqual(headers, ["First Name", "Last Name", "Grade"])
