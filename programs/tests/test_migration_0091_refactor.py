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
