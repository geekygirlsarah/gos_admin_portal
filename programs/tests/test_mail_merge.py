import json
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.models import AttendanceSession
from organizations.models import Organization, UserOrganizationMembership
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Group,
    Program,
    ProgramFeature,
    RolePermission,
    Student,
    Team,
)
from programs.utils.mail_merge import (
    analyze_merge_fields,
    parse_merge_data,
    substitute_html_fields,
    substitute_subject,
    substitute_text_fields,
)


class MailMergeRenderingTests(SimpleTestCase):
    def test_html_values_are_escaped_and_newlines_become_breaks(self):
        rendered = substitute_html_fields(
            "<p>{{ first_name }}<br>{{ note }}</p>",
            {
                "first_name": "<script>alert(1)</script>",
                "note": "first line\nsecond line",
            },
        )

        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)
        self.assertIn("first line<br>second line", rendered)
        self.assertNotIn("<script>", rendered)

    def test_text_values_remain_text_and_keep_line_breaks(self):
        rendered = substitute_text_fields(
            "Hello {{ first_name }}: {{ note }}",
            {"first_name": "A&B", "note": "one\ntwo"},
        )

        self.assertEqual(rendered, "Hello A&B: one\ntwo")

    def test_subject_values_are_single_line(self):
        rendered = substitute_subject(
            "Update for {{ first_name }}",
            {"first_name": "Ada\r\nBaker"},
        )

        self.assertEqual(rendered, "Update for Ada Baker")

    def test_unknown_and_html_split_fields_are_reported(self):
        result = analyze_merge_fields(
            "<p>{{ first_<strong>name }}</strong> and {{ missing_field }}</p>",
            {"first_name"},
        )

        self.assertEqual(result["unknown"], ["missing_field"])
        self.assertEqual(result["split"], ["first_name"])

    def test_known_plain_fields_are_not_reported(self):
        result = analyze_merge_fields("<p>{{ first_name }}</p>", {"first_name"})

        self.assertEqual(result, {"unknown": [], "split": []})

    def test_merge_fields_in_attributes_are_unsupported(self):
        result = analyze_merge_fields(
            '<p><a href="/students/{{ first_name }}">Profile</a></p>',
            {"first_name"},
        )

        self.assertEqual(result["unknown"], ["first_name"])


class MailMergeInputTests(SimpleTestCase):
    def test_grid_payload_accepts_valid_custom_column_values(self):
        payload = json.dumps(
            {
                "columns": ["volunteer_hours"],
                "rows": [
                    {"student_id": 12, "volunteer_hours": "4.5"},
                    {"student_id": 13, "volunteer_hours": "2"},
                ],
            }
        )

        self.assertEqual(
            parse_merge_data(payload),
            {
                "columns": ["volunteer_hours"],
                "rows": [
                    {"student_id": 12, "volunteer_hours": "4.5"},
                    {"student_id": 13, "volunteer_hours": "2"},
                ],
            },
        )

    def test_grid_rejects_invalid_column_names(self):
        payload = json.dumps(
            {"columns": ["{{first_name}}"], "rows": [{"student_id": 12}]}
        )

        with self.assertRaises(ValidationError):
            parse_merge_data(payload)

    def test_grid_rejects_fractional_student_ids(self):
        payload = json.dumps({"columns": [], "rows": [{"student_id": 12.5}]})

        with self.assertRaises(ValidationError):
            parse_merge_data(payload)

    def test_grid_rejects_more_than_twenty_custom_columns(self):
        payload = json.dumps(
            {
                "columns": [f"custom_{index}" for index in range(21)],
                "rows": [],
            }
        )

        with self.assertRaises(ValidationError):
            parse_merge_data(payload)

    def test_grid_rejects_too_many_rows_and_oversized_values(self):
        too_many_rows = json.dumps(
            {
                "columns": [],
                "rows": [{"student_id": index} for index in range(501)],
            }
        )
        too_long = json.dumps(
            {
                "columns": ["note"],
                "rows": [{"student_id": 1, "note": "x" * 1001}],
            }
        )

        with self.assertRaises(ValidationError):
            parse_merge_data(too_many_rows)
        with self.assertRaises(ValidationError):
            parse_merge_data(too_long)


class ProgramMailMergeViewTests(TestCase):
    def setUp(self):
        mail.outbox = []
        self.organization = Organization.objects.create(
            name="Mail Merge Alpha",
            slug="mail-merge-alpha",
            subdomain="mail-merge-alpha",
        )
        self.host = "mail-merge-alpha.localhost"
        today = timezone.localdate()
        self.program = Program.objects.create(
            organization=self.organization,
            name="Alpha Program",
            active=True,
            start_date=today - timedelta(days=30),
            end_date=today + timedelta(days=30),
        )
        self.mentor = User.objects.create_user(
            username="mail_merge_mentor",
            email="mentor@example.test",
            password="password123",  # nosec B106
        )
        UserOrganizationMembership.objects.create(
            user=self.mentor,
            organization=self.organization,
            role="Mentor",
            is_active=True,
        )
        self.client.force_login(self.mentor)
        self.permission, _ = RolePermission.objects.update_or_create(
            organization=self.organization,
            role="Mentor",
            section="mail_merge",
            defaults={"can_read": False, "can_write": False},
        )
        self.first_student = self.make_student("Ada", "Lovelace", "ada@example.test")
        self.second_student = self.make_student("Grace", "Hopper", "grace@example.test")

    def make_student(self, first_name, last_name, email, organization=None):
        organization = organization or self.organization
        student = Student.objects.create(
            organization=organization,
            legal_first_name=first_name,
            last_name=last_name,
            personal_email=email,
        )
        if organization == self.organization:
            Enrollment.objects.create(
                organization=organization,
                student=student,
                program=self.program,
                active=True,
            )
        return student

    def make_parent(
        self,
        student,
        email,
        *,
        email_updates=True,
        login_enabled=True,
        organization=None,
    ):
        parent = Adult.objects.create(
            organization=organization or self.organization,
            legal_first_name="Parent",
            last_name="Guardian",
            personal_email=email,
            email_updates=email_updates,
            login_enabled=login_enabled,
            is_parent=True,
        )
        AdultStudentRelationship.objects.create(
            organization=self.organization,
            adult=parent,
            student=student,
        )
        return parent

    def allow_mail_merge(self):
        RolePermission.objects.filter(pk=self.permission.pk).update(can_read=True)

    def post_merge(self, rows, *, action="send", subject="", body="", **extra):
        data = {
            "program": str(self.program.pk),
            "merge_data": json.dumps({"columns": ["note"], "rows": rows}),
            "subject": subject,
            "body": body,
            "action": action,
            "test_email": "preview@example.test",
            "from_account": "DEFAULT",
            "preview_index": "0",
            **extra,
        }
        return self.client.post(
            reverse("program_mail_merge"), data, HTTP_HOST=self.host
        )

    def test_mentor_is_denied_until_organization_enables_mail_merge(self):
        response = self.client.get(reverse("program_mail_merge"), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("home"))

        self.allow_mail_merge()
        response = self.client.get(reverse("program_mail_merge"), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["program"], self.program)
        self.assertContains(response, "/static/email-editor.js")

    def test_program_messaging_shows_mail_merge_link_only_when_enabled(self):
        messaging_url = reverse("program_messaging")
        response = self.client.get(
            messaging_url,
            {"program": self.program.pk},
            HTTP_HOST=self.host,
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("program_mail_merge"))

        self.allow_mail_merge()
        response = self.client.get(
            messaging_url,
            {"program": self.program.pk},
            HTTP_HOST=self.host,
        )

        self.assertContains(response, reverse("program_mail_merge"))

    def test_parent_cannot_use_mail_merge_even_if_permission_row_is_enabled(self):
        parent = User.objects.create_user(username="mail_merge_parent")
        UserOrganizationMembership.objects.create(
            user=parent,
            organization=self.organization,
            role="Parent",
            is_active=True,
        )
        RolePermission.objects.update_or_create(
            organization=self.organization,
            role="Parent",
            section="mail_merge",
            defaults={"can_read": True, "can_write": True},
        )
        self.client.force_login(parent)

        response = self.client.get(reverse("program_mail_merge"), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("home"))

    def test_lead_mentor_can_use_mail_merge_without_mentor_permission(self):
        lead = User.objects.create_user(username="mail_merge_lead")
        UserOrganizationMembership.objects.create(
            user=lead,
            organization=self.organization,
            role="LeadMentor",
            is_active=True,
        )
        self.client.force_login(lead)

        response = self.client.get(reverse("program_mail_merge"), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["program"], self.program)

    def test_lead_program_selector_groups_labeled_programs_and_defaults_to_current(
        self,
    ):
        today = timezone.localdate()
        upcoming_program = Program.objects.create(
            organization=self.organization,
            name="A Upcoming Program",
            active=True,
            start_date=today + timedelta(days=30),
            end_date=today + timedelta(days=60),
        )
        same_year_past_program = Program.objects.create(
            organization=self.organization,
            name="Duplicate Program",
            active=False,
            start_date=date(today.year - 2, 1, 1),
            end_date=date(today.year - 2, 12, 31),
        )
        multi_year_past_program = Program.objects.create(
            organization=self.organization,
            name="Duplicate Program",
            active=True,
            start_date=date(today.year - 2, 9, 1),
            end_date=date(today.year - 1, 5, 31),
        )
        lead = User.objects.create_user(username="mail_merge_selector_lead")
        UserOrganizationMembership.objects.create(
            user=lead,
            organization=self.organization,
            role="LeadMentor",
            is_active=True,
        )
        self.client.force_login(lead)

        response = self.client.get(reverse("program_mail_merge"), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["program"], self.program)
        self.assertContains(response, 'optgroup label="Active Programs"')
        self.assertContains(response, 'optgroup label="Upcoming Programs"')
        self.assertContains(response, 'optgroup label="Past / Archived Programs"')
        self.assertContains(
            response, f"{self.program.name} ({self.program.year_display})"
        )
        self.assertContains(
            response, f"{upcoming_program.name} ({upcoming_program.year_display})"
        )
        self.assertContains(
            response,
            f"{same_year_past_program.name} ({same_year_past_program.year_display})",
        )
        self.assertContains(
            response,
            f"{multi_year_past_program.name} ({multi_year_past_program.year_display})",
        )
        self.assertContains(response, "Show past &amp; archived programs")
        self.assertContains(response, "js-toggle-past-programs")

        response = self.client.get(
            reverse("program_mail_merge"),
            {"program": multi_year_past_program.pk},
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["program"], multi_year_past_program)
        self.assertContains(response, f'value="{multi_year_past_program.pk}" selected')

    def test_lead_does_not_default_to_a_past_program_when_no_current_programs_exist(
        self,
    ):
        Program.objects.filter(pk=self.program.pk).update(
            end_date=timezone.localdate() - timedelta(days=1)
        )
        lead = User.objects.create_user(username="mail_merge_past_only_lead")
        UserOrganizationMembership.objects.create(
            user=lead,
            organization=self.organization,
            role="LeadMentor",
            is_active=True,
        )
        self.client.force_login(lead)

        response = self.client.get(reverse("program_mail_merge"), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["program"])

    def test_mentor_cannot_mail_merge_a_program_they_cannot_view(self):
        self.allow_mail_merge()
        past_program = Program.objects.create(
            organization=self.organization,
            name="Past Program",
            active=True,
            start_date=timezone.localdate() - timedelta(days=60),
            end_date=timezone.localdate() - timedelta(days=1),
        )

        response = self.client.get(
            reverse("program_mail_merge"),
            {"program": past_program.pk},
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("home"))

    def test_mentor_mail_merge_requires_program_read_permission(self):
        self.allow_mail_merge()
        RolePermission.objects.update_or_create(
            organization=self.organization,
            role="Mentor",
            section="programs",
            defaults={"can_read": False, "can_write": False},
        )

        response = self.client.get(
            reverse("program_mail_merge"),
            {"program": self.program.pk},
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("home"))

    def test_selected_foreign_student_id_is_rejected_without_sending(self):
        self.allow_mail_merge()
        foreign_org = Organization.objects.create(
            name="Mail Merge Beta",
            slug="mail-merge-beta",
            subdomain="mail-merge-beta",
        )
        foreign_student = self.make_student(
            "Foreign", "Student", "foreign@example.test", organization=foreign_org
        )

        response = self.post_merge(
            [{"student_id": foreign_student.pk, "note": "not local"}],
            subject="Hello {{ first_name }}",
            body="<p>{{ note }}</p>",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no longer in this program")
        self.assertEqual(mail.outbox, [])

    def test_group_filters_use_or_and_preserve_team_restriction(self):
        self.allow_mail_merge()
        group_a = Group.objects.create(
            organization=self.organization, program=self.program, name="Group A"
        )
        group_b = Group.objects.create(
            organization=self.organization, program=self.program, name="Group B"
        )
        team = Team.objects.create(
            organization=self.organization, team_type="FRC", number=1234
        )
        first = Enrollment.objects.get(student=self.first_student, program=self.program)
        second = Enrollment.objects.get(
            student=self.second_student, program=self.program
        )
        first.groups.add(group_a, group_b)
        second.groups.add(group_b)
        second.team = team
        second.save(update_fields=["team"])
        unassigned = self.make_student("Unassigned", "Student", "none@example.test")
        url = reverse("program_mail_merge")

        response = self.client.get(
            url, {"program": self.program.pk}, HTTP_HOST=self.host
        )
        self.assertEqual(
            {row["student"].pk for row in response.context["merge_rows"]},
            {self.first_student.pk, self.second_student.pk, unassigned.pk},
        )
        self.assertContains(response, "No groups selected means no group restriction")
        response = self.client.get(
            url,
            {"program": self.program.pk, "groups": [group_a.pk, group_b.pk]},
            HTTP_HOST=self.host,
        )
        self.assertEqual(
            [row["student"].pk for row in response.context["merge_rows"]],
            [self.first_student.pk, self.second_student.pk],
        )
        self.assertContains(response, f'name="groups" value="{group_a.pk}"')
        self.assertContains(response, f'name="groups" value="{group_b.pk}"')
        response = self.client.get(
            url,
            {
                "program": self.program.pk,
                "groups": [group_a.pk, group_b.pk],
                "teams": [team.pk],
            },
            HTTP_HOST=self.host,
        )
        self.assertEqual(
            [row["student"].pk for row in response.context["merge_rows"]],
            [self.second_student.pk],
        )
        response = self.post_merge(
            [{"student_id": self.first_student.pk, "note": "excluded"}],
            action="confirm",
            subject="Hello",
            body="<p>Hi</p>",
            groups=[group_b.pk],
            teams=[team.pk],
        )
        self.assertContains(response, "no longer in this program")
        self.assertEqual(mail.outbox, [])

    def test_forged_groups_are_rejected_on_get_and_post(self):
        other_org = Organization.objects.create(
            name="Other Org", slug="other-merge-org", subdomain="other-merge-org"
        )
        other_program = Program.objects.create(
            organization=self.organization, name="Other Program"
        )
        foreign = Group.objects.create(
            organization=other_org, program=self.program, name="Foreign Group"
        )
        wrong_program = Group.objects.create(
            organization=self.organization, program=other_program, name="Wrong Program"
        )
        for group in (foreign, wrong_program):
            with self.subTest(group=group):
                with patch(
                    "programs.views.mail_merge.get_user_role",
                    return_value="LeadMentor",
                ):
                    response = self.client.get(
                        reverse("program_mail_merge"),
                        {"program": self.program.pk, "groups": [group.pk]},
                        HTTP_HOST=self.host,
                    )
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(response["Location"], reverse("home"))
                    response = self.post_merge(
                        [{"student_id": self.first_student.pk, "note": "value"}],
                        action="confirm",
                        subject="Hello",
                        body="<p>Hi</p>",
                        groups=[group.pk],
                    )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], reverse("home"))
                self.assertEqual(mail.outbox, [])

    def test_roster_is_sorted_by_display_name_with_preferred_first_name(self):
        self.allow_mail_merge()
        preferred = self.make_student("Zelda", "Brown", "zelda@example.test")
        preferred.preferred_first_name = "Bea"
        preferred.save(update_fields=["preferred_first_name"])
        blank_preferred = self.make_student("carla", "Diaz", "carla@example.test")
        blank_preferred.preferred_first_name = ""
        blank_preferred.save(update_fields=["preferred_first_name"])

        response = self.client.get(
            reverse("program_mail_merge"),
            {"program": self.program.pk},
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["values"]["first_name"] for row in response.context["merge_rows"]],
            ["Ada", "Bea", "carla", "Grace"],
        )

    def test_roster_and_program_list_exclude_other_organizations(self):
        self.allow_mail_merge()
        foreign_org = Organization.objects.create(
            name="Mail Merge Gamma",
            slug="mail-merge-gamma",
            subdomain="mail-merge-gamma",
        )
        foreign_program = Program.objects.create(
            organization=foreign_org,
            name="Alpha Program",
            active=True,
            start_date=self.program.start_date,
            end_date=self.program.end_date,
        )
        foreign_student = self.make_student(
            "Outsider", "Elsewhere", "outsider@example.test", organization=foreign_org
        )
        Enrollment.objects.create(
            organization=foreign_org,
            student=foreign_student,
            program=foreign_program,
            active=True,
        )

        response = self.client.get(
            reverse("program_mail_merge"),
            {"program": self.program.pk},
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [option.pk for option in response.context["programs"]], [self.program.pk]
        )
        self.assertEqual(
            {row["student"].pk for row in response.context["merge_rows"]},
            {self.first_student.pk, self.second_student.pk},
        )
        self.assertNotContains(response, "Outsider")
        self.assertContains(response, 'id="merge-how-it-works"')
        self.assertContains(response, "Only students enrolled in your organization")

        response = self.client.get(
            reverse("program_mail_merge"),
            {"program": foreign_program.pk},
            HTTP_HOST=self.host,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("home"))

    def test_empty_selection_never_falls_back_to_all_students(self):
        self.allow_mail_merge()

        response = self.post_merge([], subject="Hello", body="<p>Message</p>")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select at least one student")
        self.assertEqual(mail.outbox, [])

    def test_each_student_gets_their_own_values_and_html_is_escaped(self):
        self.allow_mail_merge()

        response = self.post_merge(
            [
                {"student_id": self.first_student.pk, "note": "<script>bad()</script>"},
                {"student_id": self.second_student.pk, "note": "safe note"},
            ],
            subject="Hello {{ first_name }}",
            body="<p>{{ first_name }}: {{ note }}</p>",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 2)
        messages_by_recipient = {message.to[0]: message for message in mail.outbox}
        self.assertEqual(messages_by_recipient["ada@example.test"].subject, "Hello Ada")
        self.assertEqual(
            messages_by_recipient["grace@example.test"].subject, "Hello Grace"
        )
        ada_message = messages_by_recipient["ada@example.test"]
        self.assertIsInstance(ada_message, EmailMultiAlternatives)
        ada_html = ada_message.alternatives[0][0]
        self.assertIn("&lt;script&gt;bad()&lt;/script&gt;", ada_html)
        self.assertNotIn("<script>bad()", ada_html)
        self.assertTrue(
            all(
                getattr(message, "_momentum_delivery_tracking", False)
                for message in mail.outbox
            )
        )

    def test_test_send_renders_only_the_preview_row_and_is_untracked(self):
        self.allow_mail_merge()
        self.make_parent(self.second_student, "parent@example.test")

        response = self.post_merge(
            [
                {"student_id": self.first_student.pk, "note": "first row"},
                {"student_id": self.second_student.pk, "note": "second row"},
            ],
            action="test_send",
            subject="Hello {{ first_name }}",
            body="<p>{{ note }}</p>",
            preview_index="1",
            recipients="students_and_parents",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["preview@example.test"])
        self.assertIn("second row", mail.outbox[0].body)
        self.assertNotIn("first row", mail.outbox[0].body)
        self.assertFalse(getattr(mail.outbox[0], "_momentum_delivery_tracking", True))

    def test_grid_hides_student_email_and_program_but_keeps_merge_fields(self):
        self.allow_mail_merge()

        response = self.client.get(reverse("program_mail_merge"), HTTP_HOST=self.host)

        grid_fields = {field["key"] for field in response.context["grid_fields"]}
        field_options = {field for field, _ in response.context["field_options"]}
        self.assertNotIn("email", grid_fields)
        self.assertNotIn("program_name", grid_fields)
        self.assertIn("email", field_options)
        self.assertIn("program_name", field_options)
        self.assertNotContains(response, self.first_student.personal_email)
        self.assertContains(response, 'value="students_and_parents"')
        self.assertContains(response, 'id="merge-review-count">2</span>')
        self.assertContains(response, "student(s)")
        self.assertContains(response, 'id="merge-review-parents"')
        self.assertContains(response, "and parent(s)")

    def test_students_and_parents_mode_sends_individual_tracked_local_opt_in_copies(
        self,
    ):
        self.allow_mail_merge()
        self.make_parent(self.first_student, "parent@example.test")
        self.make_parent(
            self.first_student,
            "opted-out@example.test",
            email_updates=False,
        )
        self.make_parent(
            self.first_student,
            "disabled-account@example.test",
            login_enabled=False,
        )
        other_organization = Organization.objects.create(
            name="Mail Merge Beta",
            slug="mail-merge-beta",
            subdomain="mail-merge-beta",
        )
        self.make_parent(
            self.first_student,
            "foreign-parent@example.test",
            organization=other_organization,
        )

        response = self.post_merge(
            [{"student_id": self.first_student.pk, "note": "private row value"}],
            subject="Hello {{ first_name }}",
            body="<p>{{ note }}</p>",
            recipients="students_and_parents",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            {message.to[0] for message in mail.outbox},
            {"ada@example.test", "parent@example.test"},
        )
        self.assertTrue(all(len(message.to) == 1 for message in mail.outbox))
        self.assertTrue(
            all(
                getattr(message, "_momentum_delivery_tracking", False)
                for message in mail.outbox
            )
        )
        self.assertTrue(
            all("private row value" in message.body for message in mail.outbox)
        )

    def test_students_only_is_default_and_invalid_recipient_mode_falls_back_to_students(
        self,
    ):
        self.allow_mail_merge()
        self.make_parent(self.first_student, "parent@example.test")

        response = self.post_merge(
            [{"student_id": self.first_student.pk, "note": "value"}],
            subject="Hello {{ first_name }}",
            body="<p>{{ note }}</p>",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            [message.to for message in mail.outbox], [["ada@example.test"]]
        )

        mail.outbox = []
        response = self.post_merge(
            [{"student_id": self.first_student.pk, "note": "value"}],
            subject="Hello {{ first_name }}",
            body="<p>{{ note }}</p>",
            recipients="invalid-mode",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            [message.to for message in mail.outbox], [["ada@example.test"]]
        )

    def test_confirmation_count_includes_parent_recipients(self):
        self.allow_mail_merge()
        self.make_parent(self.first_student, "parent@example.test")

        response = self.post_merge(
            [{"student_id": self.first_student.pk, "note": "value"}],
            action="confirm",
            subject="Hello {{ first_name }}",
            body="<p>{{ note }}</p>",
            recipients="students_and_parents",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["student_recipient_count"], 1)
        self.assertEqual(response.context["parent_recipient_count"], 1)
        self.assertEqual(response.context["recipient_count"], 2)
        self.assertContains(response, "1 student(s) and 1 parent(s)?")
        self.assertContains(response, "Send 2 personalized email(s)")
        self.assertEqual(mail.outbox, [])

    def test_opted_in_parent_receives_copy_when_student_has_no_email(self):
        self.allow_mail_merge()
        student = self.make_student("No", "Address", "")
        self.make_parent(student, "parent@example.test")

        response = self.post_merge(
            [{"student_id": student.pk, "note": "value"}],
            subject="Hello {{ first_name }}",
            body="<p>{{ note }}</p>",
            recipients="students_and_parents",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            [message.to for message in mail.outbox], [["parent@example.test"]]
        )
        self.assertTrue(getattr(mail.outbox[0], "_momentum_delivery_tracking", False))

    def test_unknown_merge_field_blocks_test_and_full_send(self):
        self.allow_mail_merge()

        response = self.post_merge(
            [{"student_id": self.first_student.pk, "note": "value"}],
            subject="Hello {{ first_name }}",
            body="<p>{{ unknown_field }}</p>",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fix unknown or formatting-split")
        self.assertEqual(mail.outbox, [])

    def test_attendance_fields_require_program_feature_and_read_permission(self):
        attendance_feature, _ = ProgramFeature.objects.get_or_create(
            organization=self.organization,
            key="attendance",
            defaults={"name": "Attendance"},
        )
        self.program.features.add(attendance_feature)
        AttendanceSession.objects.create(
            organization=self.organization,
            program=self.program,
            student=self.first_student,
            check_in=timezone.now() - timedelta(hours=3),
            duration_minutes=125,
        )
        self.allow_mail_merge()

        response = self.client.get(reverse("program_mail_merge"), HTTP_HOST=self.host)
        self.assertNotIn(
            "attendance_total_hours",
            [field for field, _ in response.context["field_options"]],
        )

        RolePermission.objects.update_or_create(
            organization=self.organization,
            role="Mentor",
            section="attendance",
            defaults={"can_read": True, "can_write": False},
        )
        response = self.client.get(reverse("program_mail_merge"), HTTP_HOST=self.host)

        self.assertIn(
            "attendance_total_hours",
            [field for field, _ in response.context["field_options"]],
        )
        values = response.context["merge_rows"][0]["values"]
        self.assertEqual(values["attendance_total_hours"], "2.1")

    def test_grid_offers_column_group_toggles_only_for_visible_groups(self):
        self.allow_mail_merge()

        response = self.client.get(reverse("program_mail_merge"), HTTP_HOST=self.host)

        group_keys = [group["key"] for group in response.context["grid_column_groups"]]
        self.assertEqual(group_keys, ["details", "groups"])
        self.assertNotContains(response, 'data-column-group="attendance"')
        self.assertNotContains(response, 'data-column-group="outreach"')
        self.assertContains(response, 'id="merge-column-modal"')
        rendered = response.content.decode()
        self.assertLess(
            rendered.index('id="add-merge-column"'),
            rendered.index('id="merge-grid"'),
        )
        self.assertGreater(
            rendered.index("Email recipients"),
            rendered.index("3. Compose and preview"),
        )
        self.assertGreater(
            rendered.index('id="merge-column-modal"'),
            rendered.index('id="mailMergeForm"'),
        )
        fields = {field["key"]: field for field in response.context["grid_fields"]}
        self.assertTrue(fields["first_name"]["sticky"])
        self.assertTrue(fields["last_name"]["sticky"])
        self.assertEqual(fields["first_name"]["group"], "")
        self.assertNotIn("email", fields)
        self.assertNotIn("program_name", fields)
        self.assertEqual(fields["team_name"]["group"], "groups")
        self.assertContains(response, 'class="merge-sticky-col"')

        attendance_feature, _ = ProgramFeature.objects.get_or_create(
            organization=self.organization,
            key="attendance",
            defaults={"name": "Attendance"},
        )
        self.program.features.add(attendance_feature)
        RolePermission.objects.update_or_create(
            organization=self.organization,
            role="Mentor",
            section="attendance",
            defaults={"can_read": True, "can_write": False},
        )
        response = self.client.get(reverse("program_mail_merge"), HTTP_HOST=self.host)

        group_keys = [group["key"] for group in response.context["grid_column_groups"]]
        self.assertEqual(group_keys, ["details", "groups", "attendance"])
        self.assertContains(response, 'id="merge-toggle-attendance"')
        self.assertContains(response, '<td data-column-group="attendance">')


class MailMergePermissionSettingsTests(TestCase):
    def test_settings_page_shows_mentor_only_read_toggle_by_default(self):
        organization = Organization.objects.create(
            name="Mail Merge Settings",
            slug="mail-merge-settings",
            subdomain="mail-merge-settings",
        )
        lead = User.objects.create_user(username="mail_merge_settings_lead")
        UserOrganizationMembership.objects.create(
            user=lead,
            organization=organization,
            role="LeadMentor",
            is_active=True,
        )
        self.client.force_login(lead)

        response = self.client.get(
            reverse("portal_settings"),
            HTTP_HOST="mail-merge-settings.localhost",
        )

        self.assertEqual(response.status_code, 200)
        row = next(
            item
            for item in response.context["grouped_permissions"]
            if item["name"] == "Messaging - Mail Merge"
        )
        self.assertFalse(row["mentor"].can_read)
        self.assertFalse(row["mentor"].can_write)
        self.assertIsNone(row["parent"])
        self.assertIsNone(row["student"])
        self.assertTrue(row["uses_read"])
        self.assertFalse(row["uses_write"])

        permission = row["mentor"]
        response = self.client.post(
            reverse("portal_permissions_update"),
            {f"perm_{permission.pk}": "read"},
            HTTP_HOST="mail-merge-settings.localhost",
        )

        self.assertEqual(response.status_code, 302)
        permission.refresh_from_db()
        self.assertTrue(permission.can_read)
        self.assertFalse(permission.can_write)

        self.client.post(
            reverse("portal_permissions_update"),
            {f"perm_{permission.pk}": "write"},
            HTTP_HOST="mail-merge-settings.localhost",
        )
        permission.refresh_from_db()
        self.assertTrue(permission.can_read)
        self.assertFalse(permission.can_write)
