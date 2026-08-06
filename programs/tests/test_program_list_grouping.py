from datetime import date

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from programs.models import Program


class ProgramListGroupingTests(TestCase):
    """Past programs on the landing page should be grouped by the school
    year (July–June) in which they end."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="lm", password="pass12345"
        )  # nosec B106
        group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(group)
        self.client.login(username="lm", password="pass12345")  # nosec B106

    def _make_past_program(self, name, end_date):
        return Program.objects.create(name=name, end_date=end_date)

    def _labels(self):
        response = self.client.get(reverse("program_list"))
        return [label for label, _ in response.context["past_programs_by_year"]]

    def test_past_programs_grouped_by_school_year(self):
        # Relative to "today" so the test stays valid regardless of when it runs.
        # School year = July through June, keyed by the year it ends in.
        # e.g. a program ending June 2025 belongs to 2024-2025;
        #      a program ending July 2025 belongs to 2025-2026.
        last_year = date.today().year - 1
        two_years_ago = date.today().year - 2

        june_last = self._make_past_program("June of last year", date(last_year, 6, 30))
        july_last = self._make_past_program("July of last year", date(last_year, 7, 31))
        june_two_ago = self._make_past_program(
            "June of two years ago", date(two_years_ago, 6, 30)
        )
        july_two_ago = self._make_past_program(
            "July of two years ago", date(two_years_ago, 7, 31)
        )

        response = self.client.get(reverse("program_list"))
        grouped = dict(response.context["past_programs_by_year"])

        # June of two years ago (before July) -> 2023-2024
        self.assertEqual(
            set(grouped[f"{two_years_ago - 1}-{two_years_ago}"]), {june_two_ago}
        )
        # July of two years ago AND June of last year both end in 2024-2025
        self.assertEqual(
            set(grouped[f"{two_years_ago}-{two_years_ago + 1}"]),
            {july_two_ago, june_last},
        )
        # July of last year -> 2025-2026
        self.assertEqual(set(grouped[f"{last_year}-{last_year + 1}"]), {july_last})

    def test_past_program_groups_newest_first(self):
        last_year = date.today().year - 1
        two_years_ago = date.today().year - 2
        self._make_past_program("Old", date(two_years_ago, 6, 30))
        self._make_past_program("New", date(last_year, 7, 1))

        labels = self._labels()
        self.assertEqual(
            labels,
            [f"{last_year}-{last_year + 1}", f"{two_years_ago - 1}-{two_years_ago}"],
        )

    def test_programs_without_end_date_not_grouped_as_past(self):
        today = date.today()
        Program.objects.create(name="No end date", start_date=today)

        response = self.client.get(reverse("program_list"))
        grouped = dict(response.context["past_programs_by_year"])
        self.assertEqual(sum(len(programs) for programs in grouped.values()), 0)
