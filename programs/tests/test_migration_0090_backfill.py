from django.test import TestCase

from programs.models import Adult, AdultStudentRelationship, Student


class StudentParentLinkInvariantTest(TestCase):
    """Regression coverage for the 0090/0091 parent-link migrations.

    0090 backfilled AdultStudentRelationship rows from the legacy
    Student.primary_contact / secondary_contact FKs, and 0091 made those
    relationship rows the single source of truth (Student now points at the
    through rows). The scenario that prompted 0090 was "unidirectional drift":
    a Student whose contact was set while `Adult.all_students` (M2M-only) did
    not show the student. These tests lock in the post-refactor invariant that
    assigning a contact creates the through row and the pointer together, so
    both sides always see each other.
    """

    def test_setting_primary_contact_keeps_both_sides_in_sync(self):
        parent = Adult.objects.create(legal_first_name="Prim", last_name="Parent")
        student = Student.objects.create(
            legal_first_name="Kid", last_name="Student", primary_contact=parent
        )

        self.assertEqual(list(student.all_parents), [parent])
        self.assertEqual([s.pk for s in parent.all_students()], [student.pk])
        self.assertTrue(
            AdultStudentRelationship.objects.filter(
                adult=parent, student=student
            ).exists()
        )

    def test_setting_primary_and_secondary_creates_two_through_rows(self):
        primary = Adult.objects.create(legal_first_name="Prim", last_name="Parent")
        secondary = Adult.objects.create(legal_first_name="Sec", last_name="Parent")
        student = Student.objects.create(
            preferred_first_name="Kid",
            last_name="Student",
            primary_contact=primary,
            secondary_contact=secondary,
        )

        rels = AdultStudentRelationship.objects.filter(student=student)
        self.assertEqual(rels.count(), 2)
        self.assertEqual(student.primary_contact, primary)
        self.assertEqual(student.secondary_contact, secondary)
        self.assertEqual(
            set(primary.all_students()[0].pk for _ in [0]),
            {student.pk},
        )
        self.assertIn(student, secondary.all_students())

    def test_all_parents_dedupes_primary_secondary_and_m2m(self):
        primary = Adult.objects.create(legal_first_name="Prim", last_name="Parent")
        secondary = Adult.objects.create(legal_first_name="Sec", last_name="Parent")
        student = Student.objects.create(
            preferred_first_name="Kid",
            last_name="Student",
            primary_contact=primary,
            secondary_contact=secondary,
        )
        # Adding via the M2M must not create a duplicate through row.
        student.adults.add(primary)

        self.assertEqual(len(student.all_parents), 2)
        self.assertEqual(
            AdultStudentRelationship.objects.filter(student=student).count(), 2
        )
