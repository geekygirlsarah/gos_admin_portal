import datetime

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.views.review import ApplicationEmailForm
from orders.forms import OrderForm
from programs.forms import ProgramEmailBalancesForm, ProgramEmailForm
from programs.models import Program, ProgramFeature
from programs.utils.programs import (
    get_grouped_program_choices,
    group_programs_by_status,
)


class ProgramGroupingUtilsTests(TestCase):
    def setUp(self):
        today = timezone.now().date()
        # Active programs (active=True, start <= today <= end)
        self.prog_active_b = Program.objects.create(
            name="Beta FRC 2026",
            active=True,
            start_date=today - datetime.timedelta(days=30),
            end_date=today + datetime.timedelta(days=60),
        )
        self.prog_active_a = Program.objects.create(
            name="Alpha FTC 2026",
            active=True,
            start_date=today - datetime.timedelta(days=10),
            end_date=today + datetime.timedelta(days=100),
        )
        # Upcoming programs (active=True, start > today)
        self.prog_upcoming_far = Program.objects.create(
            name="Zeta Summer 2027",
            active=True,
            start_date=today + datetime.timedelta(days=90),
            end_date=today + datetime.timedelta(days=120),
        )
        self.prog_upcoming_soon = Program.objects.create(
            name="Delta Spring 2027",
            active=True,
            start_date=today + datetime.timedelta(days=20),
            end_date=today + datetime.timedelta(days=50),
        )
        # Past programs (end < today or active=False)
        self.prog_past_recent = Program.objects.create(
            name="Gamma FRC 2025",
            active=True,
            start_date=today - datetime.timedelta(days=400),
            end_date=today - datetime.timedelta(days=40),
        )
        self.prog_past_old = Program.objects.create(
            name="Epsilon FRC 2020",
            active=False,
            start_date=today - datetime.timedelta(days=2000),
            end_date=today - datetime.timedelta(days=1700),
        )

    def test_group_programs_by_status(self):
        grouped = group_programs_by_status(Program.objects.all())
        self.assertEqual(len(grouped["active"]), 2)
        # Active should be sorted alphabetically by name (Alpha before Beta)
        self.assertEqual(grouped["active"][0].name, "Alpha FTC 2026")
        self.assertEqual(grouped["active"][1].name, "Beta FRC 2026")

        self.assertEqual(len(grouped["upcoming"]), 2)
        # Upcoming should be sorted chronologically by start_date ascending (soonest first: Delta before Zeta)
        self.assertEqual(grouped["upcoming"][0].name, "Delta Spring 2027")
        self.assertEqual(grouped["upcoming"][1].name, "Zeta Summer 2027")

        self.assertEqual(len(grouped["past"]), 2)
        # Past should be sorted reverse-chronologically (newest past first: Gamma 2025 before Epsilon 2020)
        self.assertEqual(grouped["past"][0].name, "Gamma FRC 2025")
        self.assertEqual(grouped["past"][1].name, "Epsilon FRC 2020")

    def test_get_grouped_program_choices(self):
        choices = get_grouped_program_choices(
            Program.objects.all(), empty_label="--- Select a Program ---"
        )
        # Structure: [('', '--- Select a Program ---'), ('Active Programs', [...]), ('Upcoming Programs', [...]), ('Past / Archived Programs', [...])]
        self.assertEqual(choices[0], ("", "--- Select a Program ---"))
        labels = [c[0] for c in choices[1:]]
        self.assertIn("Active Programs", labels)
        self.assertIn("Upcoming Programs", labels)
        self.assertIn("Past / Archived Programs", labels)

        # Without past programs
        choices_no_past = get_grouped_program_choices(
            Program.objects.all(), include_past=False
        )
        labels_no_past = [c[0] for c in choices_no_past]
        self.assertIn("Active Programs", labels_no_past)
        self.assertIn("Upcoming Programs", labels_no_past)
        self.assertNotIn("Past / Archived Programs", labels_no_past)


class ProgramGroupingFormAndViewsTests(TestCase):
    def setUp(self):
        today = timezone.now().date()
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.mentor_group, _ = Group.objects.get_or_create(name="Mentor")

        self.lead_user = User.objects.create_user(
            username="lead", email="lead@example.com", password="password"  # nosec B106
        )
        self.lead_user.groups.add(self.lead_group)

        self.mentor_user = User.objects.create_user(
            username="mentor",
            email="mentor@example.com",
            password="password",  # nosec B106
        )
        self.mentor_user.groups.add(self.mentor_group)

        self.prog_active = Program.objects.create(
            name="Active Program 2026",
            active=True,
            start_date=today - datetime.timedelta(days=10),
            end_date=today + datetime.timedelta(days=100),
        )
        self.prog_upcoming = Program.objects.create(
            name="Upcoming Program 2027",
            active=True,
            start_date=today + datetime.timedelta(days=30),
            end_date=today + datetime.timedelta(days=90),
        )
        self.prog_past = Program.objects.create(
            name="Past Program 2024",
            active=True,
            start_date=today - datetime.timedelta(days=500),
            end_date=today - datetime.timedelta(days=200),
        )
        feat_attendance, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        self.prog_active.features.add(feat_attendance)
        self.prog_upcoming.features.add(feat_attendance)
        self.prog_past.features.add(feat_attendance)

    def test_program_email_form_grouped_choices(self):
        form_lead = ProgramEmailForm(user=self.lead_user)
        choices = list(form_lead.fields["program"].choices)
        choice_labels = [c[0] for c in choices if isinstance(c[1], (list, tuple))]
        self.assertIn("Active Programs", choice_labels)
        self.assertIn("Upcoming Programs", choice_labels)
        self.assertIn("Past / Archived Programs", choice_labels)

        # Mentor should only see active/upcoming
        form_mentor = ProgramEmailForm(user=self.mentor_user)
        mentor_choices = list(form_mentor.fields["program"].choices)
        mentor_labels = [
            c[0] for c in mentor_choices if isinstance(c[1], (list, tuple))
        ]
        self.assertIn("Active Programs", mentor_labels)
        self.assertNotIn("Past / Archived Programs", mentor_labels)

    def test_messaging_page_renders_optgroups_and_past_toggle(self):
        self.client.force_login(self.lead_user)
        response = self.client.get(reverse("program_messaging"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn('optgroup label="Active Programs"', content)
        self.assertIn('optgroup label="Upcoming Programs"', content)
        self.assertIn('optgroup label="Past / Archived Programs"', content)
        self.assertIn("Show past &amp; archived programs", content)
        self.assertIn("js-toggle-past-programs", content)

    def test_messaging_page_preselected_past_program(self):
        self.client.force_login(self.lead_user)
        response = self.client.get(
            reverse("program_email", kwargs={"pk": self.prog_past.pk})
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn(str(self.prog_past.pk), content)
        self.assertIn(self.prog_past.name, content)

    def test_order_form_grouped_choices(self):
        form = OrderForm()
        choices = list(form.fields["program"].choices)
        choice_labels = [c[0] for c in choices if isinstance(c[1], (list, tuple))]
        self.assertIn("Active Programs", choice_labels)
        self.assertIn("Upcoming Programs", choice_labels)
        self.assertIn("Past / Archived Programs", choice_labels)

    def test_application_email_form_grouped_choices(self):
        form = ApplicationEmailForm()
        choices = list(form.fields["program"].choices)
        choice_labels = [c[0] for c in choices if isinstance(c[1], (list, tuple))]
        self.assertIn("Active Programs", choice_labels)
        self.assertIn("Upcoming Programs", choice_labels)
        self.assertIn("Past / Archived Programs", choice_labels)

    def test_application_review_list_renders_optgroups(self):
        from django.contrib.auth.models import Permission

        perm = Permission.objects.get(codename="review_application")
        self.lead_user.user_permissions.add(perm)
        self.client.force_login(self.lead_user)
        response = self.client.get(reverse("application_review_list"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn('optgroup label="Active Programs"', content)
        self.assertIn('optgroup label="Upcoming Programs"', content)
        self.assertIn('optgroup label="Past / Archived Programs"', content)

    def test_all_attendance_renders_optgroups(self):
        self.client.force_login(self.lead_user)
        response = self.client.get(reverse("all_attendance"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn('optgroup label="Active Programs"', content)
        self.assertIn('optgroup label="Upcoming Programs"', content)
        self.assertIn('optgroup label="Past / Archived Programs"', content)

    def test_hours_chart_renders_optgroups(self):
        self.client.force_login(self.lead_user)
        response = self.client.get(reverse("attendance_hours_chart"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn('optgroup label="Active Programs"', content)
        self.assertIn('optgroup label="Upcoming Programs"', content)
        self.assertIn('optgroup label="Past / Archived Programs"', content)
