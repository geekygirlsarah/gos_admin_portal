from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.db import connection
from django.db.migrations.exceptions import IrreversibleError
from django.test import TestCase

from orders.models import OrderItem
from organizations.models import Organization
from programs.models import Crew, Enrollment, Group, Program, Student, SubTeam


class ProgramGroupDataMigrationTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            name="Group Migration Organization", slug="group-migration"
        )
        self.program = Program.objects.create(
            organization=self.organization, name="Migration Program"
        )
        self.student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Migration",
            last_name="Student",
        )
        self.crew = Crew.objects.create(
            organization=self.organization,
            program=self.program,
            name="Build",
            color="#123456",
        )
        self.subteam = SubTeam.objects.create(
            organization=self.organization,
            program=self.program,
            name="Build",
            color="#654321",
        )

    def test_existing_crews_and_subteams_are_copied_to_groups(self):
        enrollment = Enrollment.objects.create(
            organization=self.organization,
            student=self.student,
            program=self.program,
            crew=self.crew,
            subteam=self.subteam,
        )
        item = OrderItem.objects.create(
            organization=self.organization,
            program=self.program,
            item_name="Build supplies",
            crew=self.crew,
            subteam=self.subteam,
        )
        migration = import_module("orders.migrations.0011_orderitem_groups")
        migration.migrate_legacy_groups(apps, SimpleNamespace(connection=connection))

        group = Group.objects.get(
            organization=self.organization,
            program=self.program,
            name="Build",
        )
        self.assertSetEqual(set(enrollment.groups.all()), {group})
        self.assertSetEqual(set(item.groups.all()), {group})

        migration.migrate_legacy_groups(apps, SimpleNamespace(connection=connection))
        self.assertEqual(
            Group.objects.filter(
                organization=self.organization,
                program=self.program,
                name="Build",
            ).count(),
            1,
        )
        migration.refuse_unsafe_reverse(apps, SimpleNamespace(connection=connection))

    def test_reverse_refuses_group_assignments_not_represented_by_legacy_fields(self):
        enrollment = Enrollment.objects.create(
            organization=self.organization,
            student=self.student,
            program=self.program,
            crew=self.crew,
        )
        migration = import_module("orders.migrations.0011_orderitem_groups")
        migration.migrate_legacy_groups(apps, SimpleNamespace(connection=connection))
        additional_crew = Crew.objects.create(
            organization=self.organization,
            program=self.program,
            name="Electrical",
        )
        migration.migrate_legacy_groups(apps, SimpleNamespace(connection=connection))
        additional_group = Group.objects.get(
            organization=self.organization,
            program=self.program,
            name=additional_crew.name,
        )
        enrollment.groups.add(additional_group)

        with self.assertRaises(IrreversibleError):
            migration.refuse_unsafe_reverse(
                apps, SimpleNamespace(connection=connection)
            )
