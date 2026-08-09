import importlib

from django.apps import apps as real_apps
from django.test import TestCase


class BackfillStudentParentLinksMigrationTest(TestCase):
    """Regression test for migration 0090.

    Before this migration, a Student could have primary_contact /
    secondary_contact FKs set while the corresponding
    AdultStudentRelationship (M2M) row was missing. That made
    `student.all_parents` show the parent while `adult.all_students`
    (which only reads the M2M) did not show the student. The migration
    backfills the M2M rows from the FKs.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.migration_module = importlib.import_module(
            "programs.migrations.0090_backfill_student_parent_links"
        )
        cls.backfill = cls.migration_module.backfill_parent_links
        cls.Student = real_apps.get_model("programs", "Student")
        cls.Adult = real_apps.get_model("programs", "Adult")
        cls.AdultStudentRelationship = real_apps.get_model(
            "programs", "AdultStudentRelationship"
        )

    def test_backfills_primary_and_secondary_m2m_rows(self):
        primary = self.Adult.objects.create(first_name="Prim", last_name="Parent")
        secondary = self.Adult.objects.create(first_name="Sec", last_name="Parent")
        student = self.Student.objects.create(
            legal_first_name="Kid",
            last_name="Student",
            primary_contact=primary,
            secondary_contact=secondary,
        )

        BackfillStudentParentLinksMigrationTest.backfill(real_apps, None)

        rels = self.AdultStudentRelationship.objects.filter(student=student)
        rel_ids = set(rels.values_list("adult_id", flat=True))
        self.assertEqual(rels.count(), 2)
        self.assertIn(primary.id, rel_ids)
        self.assertIn(secondary.id, rel_ids)

    def test_backfills_primary_only(self):
        primary = self.Adult.objects.create(first_name="Prim", last_name="Parent")
        student = self.Student.objects.create(
            legal_first_name="Kid",
            last_name="Student",
            primary_contact=primary,
        )

        BackfillStudentParentLinksMigrationTest.backfill(real_apps, None)

        rels = self.AdultStudentRelationship.objects.filter(student=student)
        self.assertEqual(rels.count(), 1)
        self.assertEqual(rels.first().adult_id, primary.id)

    def test_does_not_duplicate_or_overwrite_existing_links(self):
        """Existing M2M rows (e.g. a grandparent link) are left untouched."""
        primary = self.Adult.objects.create(first_name="Prim", last_name="Parent")
        student = self.Student.objects.create(
            legal_first_name="Kid",
            last_name="Student",
            primary_contact=primary,
        )
        self.AdultStudentRelationship.objects.create(
            adult=primary,
            student=student,
            relationship_to_student="grandparent",
        )

        BackfillStudentParentLinksMigrationTest.backfill(real_apps, None)

        rels = self.AdultStudentRelationship.objects.filter(student=student)
        self.assertEqual(rels.count(), 1)
        self.assertEqual(rels.first().relationship_to_student, "grandparent")
