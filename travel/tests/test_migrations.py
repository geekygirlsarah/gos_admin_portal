import datetime
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class TravelFeeDataMigrationTests(TransactionTestCase):
    migrate_from = ("travel", "0005_alter_travelcostoption_organization_and_more")
    migrate_to = ("travel", "0006_travelcostoption_is_required_and_more")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps

        Organization = old_apps.get_model("organizations", "Organization")
        School = old_apps.get_model("programs", "School")
        Program = old_apps.get_model("programs", "Program")
        Student = old_apps.get_model("programs", "Student")
        TravelEvent = old_apps.get_model("travel", "TravelEvent")
        TravelCostOption = old_apps.get_model("travel", "TravelCostOption")
        TravelSignup = old_apps.get_model("travel", "TravelSignup")

        self.organization = Organization.objects.create(
            name="Migration Test",
            slug="travel-migration-test",
            subdomain="travel-migration-test",
        )
        self.other_organization = Organization.objects.create(
            name="Other Migration Test",
            slug="other-travel-migration-test",
            subdomain="other-travel-migration-test",
        )
        school = School.objects.create(
            organization=self.organization,
            name="Migration School",
        )
        program = Program.objects.create(
            organization=self.organization,
            name="Migration Program",
        )
        event = TravelEvent.objects.create(
            organization=self.organization,
            program=program,
            name="Migration Trip",
            start_date=datetime.date(2025, 6, 10),
            end_date=datetime.date(2025, 6, 12),
        )
        self.cost_option = TravelCostOption.objects.create(
            organization=self.organization,
            event=event,
            name="Existing fee",
            amount=Decimal("200.00"),
        )
        student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Approved",
            last_name="Student",
            school=school,
        )
        self.approved_signup = TravelSignup.objects.create(
            organization=self.organization,
            student=student,
            event=event,
            status="approved",
            approved_total=Decimal("200.00"),
            approved_at=datetime.datetime(2025, 6, 1, 12, tzinfo=datetime.timezone.utc),
        )
        self.missing_total_signup = TravelSignup.objects.create(
            organization=self.organization,
            student=Student.objects.create(
                organization=self.organization,
                legal_first_name="Missing",
                last_name="Total",
                school=school,
            ),
            event=event,
            status="approved",
            approved_total=None,
        )
        foreign_student = Student.objects.create(
            organization=self.other_organization,
            legal_first_name="Foreign",
            last_name="Student",
        )
        self.inconsistent_signup = TravelSignup.objects.create(
            organization=self.organization,
            student=foreign_student,
            event=event,
            status="approved",
            approved_total=Decimal("90.00"),
        )

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_fees_become_optional_and_only_valid_approvals_are_backfilled(
        self,
    ):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        new_apps = executor.loader.project_state([self.migrate_to]).apps
        TravelCostOption = new_apps.get_model("travel", "TravelCostOption")
        TravelSignup = new_apps.get_model("travel", "TravelSignup")

        option = TravelCostOption.objects.get(pk=self.cost_option.pk)
        approved_signup = TravelSignup.objects.get(pk=self.approved_signup.pk)
        missing_total_signup = TravelSignup.objects.get(pk=self.missing_total_signup.pk)
        inconsistent_signup = TravelSignup.objects.get(pk=self.inconsistent_signup.pk)

        self.assertFalse(option.is_required)
        self.assertEqual(approved_signup.balance_amount, Decimal("200.00"))
        self.assertEqual(
            approved_signup.balance_effective_date, datetime.date(2025, 6, 1)
        )
        self.assertIsNone(missing_total_signup.balance_amount)
        self.assertIsNone(inconsistent_signup.balance_amount)
