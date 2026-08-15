import importlib

from django.apps import apps as real_apps
from django.test import TestCase


class RefactorStudentParentLinksMigrationTest(TestCase):
    """Regression test for migration 0091.

    Migration 0091 swaps Student.primary_contact / secondary_contact (FKs to
    Adult) for relationship-row pointers (FKs to AdultStudentRelationship),
    backfilling the pointers from the legacy data. Because the new model's
    setters keep the pointer and the through row in sync automatically, the
    migration function must be idempotent against an already-correct dataset:
    no duplicate through rows, no pointer churn, and linked adults flagged as
    parents.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.migration_module = importlib.import_module(
            "programs.migrations.0091_refactor_student_parent_links"
        )
        cls.link = cls.migration_module.link_relationships_from_old_contacts
        cls.Student = real_apps.get_model("programs", "Student")
        cls.Adult = real_apps.get_model("programs", "Adult")
        cls.AdultStudentRelationship = real_apps.get_model(
            "programs", "AdultStudentRelationship"
        )

    def test_keeps_existing_relationships_and_points_at_through_rows(self):
        primary = self.Adult.objects.create(first_name="Prim", last_name="Parent")
        secondary = self.Adult.objects.create(first_name="Sec", last_name="Parent")
        student = self.Student.objects.create(
            legal_first_name="Kid",
            last_name="Student",
            primary_contact=primary,
            secondary_contact=secondary,
        )

        RefactorStudentParentLinksMigrationTest.link(real_apps, None)

        student.refresh_from_db()
        rels = list(self.AdultStudentRelationship.objects.filter(student=student))
        self.assertEqual(len(rels), 2)
        self.assertIsNotNone(student.primary_contact_relationship_id)
        self.assertIsNotNone(student.secondary_contact_relationship_id)
        self.assertNotEqual(
            student.primary_contact_relationship_id,
            student.secondary_contact_relationship_id,
        )
        self.assertEqual(student.primary_contact, primary)
        self.assertEqual(student.secondary_contact, secondary)

    def test_is_idempotent_and_reuses_existing_through_rows(self):
        primary = self.Adult.objects.create(first_name="Prim", last_name="Parent")
        student = self.Student.objects.create(
            legal_first_name="Kid",
            last_name="Student",
            primary_contact=primary,
        )

        RefactorStudentParentLinksMigrationTest.link(real_apps, None)
        pointer_after_first = self.Student.objects.get(
            pk=student.pk
        ).primary_contact_relationship_id
        RefactorStudentParentLinksMigrationTest.link(real_apps, None)

        self.assertEqual(
            self.AdultStudentRelationship.objects.filter(student=student).count(), 1
        )
        self.assertEqual(
            self.Student.objects.get(pk=student.pk).primary_contact_relationship_id,
            pointer_after_first,
        )

    def test_marks_linked_adults_as_parents(self):
        not_yet_parent = self.Adult.objects.create(
            first_name="Prim", last_name="Parent", is_parent=False
        )
        student = self.Student.objects.create(
            legal_first_name="Kid",
            last_name="Student",
            primary_contact=not_yet_parent,
        )

        RefactorStudentParentLinksMigrationTest.link(real_apps, None)

        not_yet_parent.refresh_from_db()
        self.assertTrue(not_yet_parent.is_parent)

    def test_no_contacts_is_no_op(self):
        student = self.Student.objects.create(
            legal_first_name="Kid", last_name="Student"
        )

        RefactorStudentParentLinksMigrationTest.link(real_apps, None)

        student.refresh_from_db()
        self.assertIsNone(student.primary_contact_relationship_id)
        self.assertIsNone(student.secondary_contact_relationship_id)
        self.assertEqual(
            self.AdultStudentRelationship.objects.filter(student=student).count(), 0
        )

    def test_flushes_deferred_fk_checks_before_dropping_legacy_columns(self):
        """The data backfill updates the new relationship FKs, which are
        DEFERRABLE INITIALLY DEFERRED on PostgreSQL. The resulting pending
        checks would make the RemoveField (DROP CONSTRAINT) operations fail
        with "cannot ALTER TABLE ... because it has pending trigger events",
        so the migration must flush them before any RemoveField runs.
        """
        from django.db import migrations

        ops = (
            RefactorStudentParentLinksMigrationTest.migration_module.Migration.operations
        )

        def op_index(predicate):
            return next(i for i, op in enumerate(ops) if isinstance(op, predicate))

        backfill_idx = op_index(migrations.RunPython)
        flush_idx = next(
            i
            for i, op in enumerate(ops)
            if isinstance(op, migrations.RunPython)
            and op.code
            is RefactorStudentParentLinksMigrationTest.migration_module.flush_deferred_fk_checks
        )
        first_removefield_idx = op_index(migrations.RemoveField)

        self.assertLess(backfill_idx, flush_idx)
        self.assertLess(flush_idx, first_removefield_idx)

    def test_flush_deferred_fk_checks_only_runs_on_postgresql(self):
        import types

        flush = (
            RefactorStudentParentLinksMigrationTest.migration_module.flush_deferred_fk_checks
        )

        for vendor, expected_calls in (("sqlite", 0), ("postgresql", 1)):
            with self.subTest(vendor=vendor):
                calls = []
                schema_editor = types.SimpleNamespace(
                    connection=types.SimpleNamespace(vendor=vendor),
                    execute=lambda sql: calls.append(sql),
                )
                flush(real_apps, schema_editor)
                self.assertEqual(len(calls), expected_calls)

        calls = []
        schema_editor = types.SimpleNamespace(
            connection=types.SimpleNamespace(vendor="postgresql"),
            execute=lambda sql: calls.append(sql),
        )
        flush(real_apps, schema_editor)
        self.assertEqual(calls, ["SET CONSTRAINTS ALL IMMEDIATE"])
