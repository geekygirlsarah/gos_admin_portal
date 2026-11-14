import csv
from io import BytesIO

from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from orders.forms import OrderItemForm
from orders.models import Order, OrderItem
from orders.tests.base import (
    make_item,
    make_lead_mentor_user,
    make_order,
    make_program,
    make_student_user,
)
from programs.models import Crew, Group, SubTeam, Team


class OrderItemStudentLabelsTests(TestCase):
    def setUp(self):
        self.program = make_program(name="Main Program")
        self.other_program = make_program(name="Other Program")
        self.lead = make_lead_mentor_user()
        self.student = make_student_user(program=self.program)

        self.team = Team.objects.create(
            team_type="FRC", number=3504, name="Girls of Steel", color="#dc3545"
        )
        self.group1 = Group.objects.create(
            organization=self.program.organization,
            program=self.program,
            name="Mechanical",
            color="#198754",
        )
        self.group2 = Group.objects.create(
            organization=self.program.organization,
            program=self.program,
            name="Controls",
            color="#0d6efd",
        )
        self.other_program_group = Group.objects.create(
            organization=self.program.organization,
            program=self.other_program,
            name="Other Program Group",
        )
        self.list_url = reverse("orders:order_list", args=[self.program.id])

    def login(self, user):
        self.client.force_login(user)

    def test_form_filters_groups_by_organization_and_program(self):
        form = OrderItemForm(
            program=self.program, organization=self.program.organization
        )
        self.assertIn(self.group1, form.fields["groups"].queryset)
        self.assertIn(self.group2, form.fields["groups"].queryset)
        self.assertNotIn(self.other_program_group, form.fields["groups"].queryset)
        self.assertEqual(
            form.fields["groups"].widget.__class__.__name__, "CheckboxSelectMultiple"
        )
        self.assertEqual(
            form.fields["groups"].help_text, "Select all groups this item is for."
        )

    def test_student_requests_item_for_multiple_groups(self):
        self.login(self.student)
        create_url = reverse("orders:item_create", args=[self.program.id])
        response = self.client.post(
            create_url,
            {
                "item_name": "Bearing Kit",
                "quantity": "2",
                "unit_price": "12.50",
                "team": str(self.team.pk),
                "groups": [str(self.group1.pk), str(self.group2.pk)],
                "vendor_choice": "",
            },
        )
        self.assertRedirects(response, self.list_url)
        item = OrderItem.objects.get(item_name="Bearing Kit")
        self.assertEqual(item.team, self.team)
        self.assertCountEqual(item.groups.all(), [self.group1, self.group2])

    def test_order_list_renders_all_group_badges(self):
        item = make_item(self.program, item_name="Bearing Kit", team=self.team)
        item.groups.add(self.group1, self.group2)
        self.login(self.lead)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "FRC 3504 Girls of Steel")
        self.assertContains(response, "Mechanical")
        self.assertContains(response, "Controls")

    def test_order_detail_renders_all_group_badges(self):
        order = make_order(self.program, created_by=self.lead)
        item = make_item(
            self.program, item_name="Motor Controller", order=order, team=self.team
        )
        item.groups.add(self.group1, self.group2)
        self.login(self.lead)
        detail_url = reverse("orders:order_detail", args=[self.program.id, order.pk])
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mechanical")
        self.assertContains(response, "Controls")

    def test_order_archive_renders_all_group_badges(self):
        order = make_order(
            self.program, created_by=self.lead, status=Order.STATUS_RECEIVED
        )
        item = make_item(
            self.program, item_name="Sensor Board", order=order, team=self.team
        )
        item.groups.add(self.group1, self.group2)
        self.login(self.lead)
        archive_url = reverse("orders:order_archive", args=[self.program.id])
        response = self.client.get(archive_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mechanical")
        self.assertContains(response, "Controls")

    def test_csv_export_includes_team_and_all_groups(self):
        make_item(
            self.program, item_name="Pneumatics Valve", team=self.team
        ).groups.add(self.group1, self.group2)
        self.login(self.lead)
        response = self.client.get(f"{self.list_url}?export=csv")
        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(response.content.decode("utf-8").splitlines()))
        headers = rows[0]
        self.assertIn("Team", headers)
        self.assertIn("Groups", headers)
        self.assertNotIn("Crew", headers)
        self.assertNotIn("Subteam", headers)

        data_row = rows[1]
        self.assertEqual(data_row[headers.index("Team")], str(self.team))
        self.assertEqual(data_row[headers.index("Groups")], "Controls, Mechanical")

    def test_xlsx_export_includes_team_and_groups(self):
        make_item(
            self.program, item_name="Pneumatics Valve", team=self.team
        ).groups.add(self.group1, self.group2)
        self.login(self.lead)
        response = self.client.get(f"{self.list_url}?export=xlsx")
        self.assertEqual(response.status_code, 200)
        worksheet = load_workbook(BytesIO(response.content)).active
        headers = [cell.value for cell in worksheet[1]]
        self.assertIn("Team", headers)
        self.assertIn("Groups", headers)
        self.assertNotIn("Crew", headers)
        self.assertNotIn("Subteam", headers)
        self.assertEqual(
            worksheet.cell(row=2, column=headers.index("Groups") + 1).value,
            "Controls, Mechanical",
        )

    def test_legacy_crew_and_subteam_fields_remain_available(self):
        crew = Crew.objects.create(
            program=self.program, name="Legacy Crew", color="#198754"
        )
        subteam = SubTeam.objects.create(
            program=self.program, name="Legacy Subteam", color="#6f42c1"
        )
        item = make_item(
            self.program,
            item_name="Omni Wheel",
            crew=crew,
            subteam=subteam,
        )
        crew.delete()
        subteam.delete()
        item.refresh_from_db()
        self.assertIsNone(item.crew)
        self.assertIsNone(item.subteam)
        self.assertTrue(OrderItem.objects.filter(pk=item.pk).exists())
