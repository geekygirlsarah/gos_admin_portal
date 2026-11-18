import csv
from datetime import date
from decimal import Decimal
from io import BytesIO, StringIO

from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from organizations.models import Organization, UserOrganizationMembership
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
        UserOrganizationMembership.objects.create(
            user=self.user,
            organization=self.program.organization,
            role="Mentor",
            is_active=True,
        )

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
        self.assertEqual(resp.status_code, 200)
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
        self.assertEqual(resp.status_code, 200)
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
        self.assertEqual(resp.status_code, 200)
        wb = load_workbook(BytesIO(resp.content))
        ws = wb.active

        first_names = [row[0].value for row in ws.iter_rows(min_row=2)]
        self.assertEqual(first_names, sorted(first_names))

    def test_export_filename_contains_program_name(self):
        url = reverse("program_student_export", args=[self.program.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
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
        UserOrganizationMembership.objects.create(
            user=self.user,
            organization=self.program.organization,
            role="LeadMentor",
            is_active=True,
        )

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
        self.assertEqual(resp.status_code, 200)
        return load_workbook(BytesIO(resp.content)).active

    def test_individual_columns_export_only_selected_fields_in_order(self):
        self.student_a.legal_first_name = "Alexandra"
        self.student_a.preferred_first_name = "Ali"
        self.student_a.save(update_fields=["legal_first_name", "preferred_first_name"])

        ws = self._sheet(
            self.client.get(
                self._url(
                    "column_selection=1&columns=student_phone"
                    "&columns=legal_first_name&columns=preferred_first_name"
                    "&columns=student_email"
                )
            )
        )

        headers = [cell.value for cell in ws[1]]
        self.assertEqual(
            headers,
            [
                "Student Email",
                "Student Phone",
                "Legal First Name",
                "Preferred First Name",
            ],
        )
        row = dict(zip(headers, ws[2]))
        self.assertEqual(row["Student Email"].value, "alice@example.com")
        self.assertEqual(row["Student Phone"].value, "555-1234")
        self.assertEqual(row["Legal First Name"].value, "Alexandra")
        self.assertEqual(row["Preferred First Name"].value, "Ali")

        response = self.client.get(
            self._url(
                "format=csv&column_selection=1&columns=student_email"
                "&columns=legal_first_name"
            )
        )
        csv_rows = list(csv.reader(StringIO(response.content.decode("utf-8"))))
        self.assertEqual(csv_rows[0], ["Student Email", "Legal First Name"])
        self.assertEqual(csv_rows[1], ["alice@example.com", "Alexandra"])

    def test_individual_selection_can_omit_base_columns_and_active_status(self):
        ws = self._sheet(
            self.client.get(
                self._url("column_selection=1&columns=first_name&columns=grade")
            )
        )

        headers = [cell.value for cell in ws[1]]
        self.assertEqual(headers, ["First Name", "Grade"])
        self.assertEqual(ws.cell(row=2, column=1).value, "Alice")
        self.assertNotIn("Active", headers)

    def test_empty_individual_selection_is_rejected(self):
        response = self.client.get(self._url("column_selection=1"))

        self.assertEqual(response.status_code, 400)

    def test_unknown_and_duplicate_individual_columns_are_ignored(self):
        ws = self._sheet(
            self.client.get(
                self._url(
                    "column_selection=1&columns=student_email"
                    "&columns=student_email&columns=unknown"
                )
            )
        )

        self.assertEqual(
            [cell.value for cell in ws[1]],
            ["Student Email"],
        )

    def test_export_modal_offers_individual_columns_with_existing_defaults(self):
        response = self.client.get(reverse("program_detail", args=[self.program.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Please protect student information and delete exported files when you are done.",
        )
        self.assertContains(response, 'type="submit" class="btn btn-primary"')
        self.assertContains(response, "Export</button>")
        self.assertNotContains(response, "Basic export")
        self.assertContains(response, 'name="column_selection" value="1"')
        for column in (
            "first_name",
            "last_name",
            "grade",
            "active",
            "student_email",
            "parent1_email",
            "total_fees",
        ):
            with self.subTest(column=column):
                self.assertContains(response, f'name="columns" value="{column}"')
        self.assertContains(
            response, 'value="first_name" id="exportColumnFirstName" checked'
        )
        self.assertContains(
            response, 'value="student_email" id="exportColumnStudentEmail" checked'
        )
        self.assertNotContains(
            response, 'value="legal_first_name" id="exportColumnLegalFirstName" checked'
        )

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

    def test_tshirt_group_exports_sizes_and_keeps_missing_values_blank(self):
        self.student_a.tshirt_size = "M"
        self.student_a.save(update_fields=["tshirt_size"])
        blank_student = Student.objects.create(
            organization=self.program.organization,
            legal_first_name="Zoe",
            last_name="Blank",
        )
        Enrollment.objects.create(
            organization=self.program.organization,
            student=blank_student,
            program=self.program,
            active=True,
        )

        ws = self._sheet(self.client.get(self._url("groups=tshirt")))
        headers = [cell.value for cell in ws[1]]
        self.assertEqual(headers[-1], "T-shirt Size")
        rows = {
            str(row[0].value or ""): row[-1].value for row in ws.iter_rows(min_row=2)
        }
        self.assertEqual(rows["Alice"], "M")
        self.assertIn(rows["Zoe"], (None, ""))

    def test_profile_groups_export_names_address_and_contact_preferences(self):
        self.student_a.legal_first_name = "Alexandra"
        self.student_a.preferred_first_name = "Ali"
        self.student_a.address = "1 Main Street"
        self.student_a.city = "Pittsburgh"
        self.student_a.state = "PA"
        self.student_a.zip_code = "15213"
        self.student_a.can_receive_texts = True
        self.student_a.directory_consent = False
        self.student_a.save()

        ws = self._sheet(
            self.client.get(self._url("groups=preferences,address,tshirt,names"))
        )
        headers = [cell.value for cell in ws[1]]
        self.assertEqual(
            headers[-9:],
            [
                "Legal First Name",
                "Preferred First Name",
                "T-shirt Size",
                "Street Address",
                "City",
                "State",
                "ZIP Code",
                "Can Receive Texts",
                "Directory Consent",
            ],
        )
        row = {str(header): cell.value for header, cell in zip(headers, ws[2])}
        self.assertEqual(row["Legal First Name"], "Alexandra")
        self.assertEqual(row["Preferred First Name"], "Ali")
        self.assertEqual(row["Street Address"], "1 Main Street")
        self.assertEqual(row["City"], "Pittsburgh")
        self.assertEqual(row["State"], "PA")
        self.assertEqual(row["ZIP Code"], "15213")
        self.assertEqual(row["Can Receive Texts"], "Yes")
        self.assertEqual(row["Directory Consent"], "No")

    def test_new_export_text_fields_are_safe_in_xlsx_and_csv(self):
        self.student_a.address = "=SUM(1,1)"
        self.student_a.save(update_fields=["address"])

        xlsx = self._sheet(self.client.get(self._url("groups=address")))
        xlsx_headers = [cell.value for cell in xlsx[1]]
        address_cell = xlsx.cell(row=2, column=xlsx_headers.index("Street Address") + 1)
        self.assertEqual(address_cell.value, "'=SUM(1,1)")
        self.assertNotEqual(address_cell.data_type, "f")

        response = self.client.get(self._url("format=csv&groups=address"))
        csv_rows = list(csv.reader(StringIO(response.content.decode("utf-8"))))
        address_index = csv_rows[0].index("Street Address")
        self.assertEqual(csv_rows[1][address_index], "'=SUM(1,1)")

    def test_export_modal_shows_new_columns_unchecked(self):
        response = self.client.get(reverse("program_detail", args=[self.program.pk]))
        self.assertEqual(response.status_code, 200)
        for column in (
            "legal_first_name",
            "preferred_first_name",
            "tshirt_size",
            "street_address",
            "city",
            "state",
            "zip_code",
            "can_receive_texts",
            "directory_consent",
        ):
            with self.subTest(column=column):
                self.assertContains(response, f'name="columns" value="{column}"')
        self.assertNotContains(
            response,
            'value="legal_first_name" id="exportColumnLegalFirstName" checked',
        )

    def test_export_excludes_students_owned_by_another_organization(self):
        other_org = Organization.objects.create(
            name="Other Organization",
            slug="other-organization",
            subdomain="other-organization",
        )
        foreign_student = Student.objects.create(
            organization=other_org,
            legal_first_name="Private",
            last_name="Student",
            tshirt_size="XL",
            address="99 Secret Street",
        )
        Enrollment.objects.create(
            organization=self.program.organization,
            student=foreign_student,
            program=self.program,
            active=True,
        )

        ws = self._sheet(self.client.get(self._url("groups=tshirt,address,names")))
        exported = [row[0].value for row in ws.iter_rows(min_row=2)]
        self.assertIn("Alice", exported)
        self.assertNotIn("Private", exported)

        selected_ws = self._sheet(
            self.client.get(
                self._url(
                    "column_selection=1&columns=first_name&columns=tshirt_size"
                    "&columns=street_address"
                )
            )
        )
        selected_rows = [row[0].value for row in selected_ws.iter_rows(min_row=2)]
        self.assertIn("Alice", selected_rows)
        self.assertNotIn("Private", selected_rows)

    def test_csv_format_returns_csv(self):
        resp = self.client.get(self._url("format=csv&groups=contact"))
        self.assertEqual(resp.status_code, 200)
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
        UserOrganizationMembership.objects.create(
            user=mentor,
            organization=self.program.organization,
            role="Mentor",
            is_active=True,
        )
        self.client.login(username="mentor", password="pass12345")  # nosec B106

        ws = self._sheet(
            self.client.get(
                self._url(
                    "groups=contact,parents,balance,names,tshirt,address,preferences"
                )
            )
        )
        headers = [cell.value for cell in ws[1]]
        self.assertEqual(headers, ["First Name", "Last Name", "Grade"])

        selected_ws = self._sheet(
            self.client.get(self._url("column_selection=1&columns=student_email"))
        )
        self.assertEqual(
            [cell.value for cell in selected_ws[1]],
            ["First Name", "Last Name", "Grade"],
        )
