from django.test import TestCase

from programs.models import Adult, RaceEthnicity, School, Student


class StudentModelTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Carnegie High")
        self.student = Student.objects.create(
            legal_first_name="Alex",
            last_name="Smith",
            school=self.school,
        )

    def test_student_save_does_not_override_parent_opt_out(self):
        # Saving a student must NOT silently flip a parent's explicit opt-out.
        # The email_updates preference is the parent's own choice and should
        # only be changed through the application wizard form validation.
        parent = Adult.objects.create(
            legal_first_name="Pat",
            last_name="Smith",
            is_parent=True,
            email_updates=False,
        )
        s = Student(legal_first_name="Riley", last_name="Jones", primary_contact=parent)
        s.save()
        parent.refresh_from_db()
        self.assertFalse(parent.email_updates)

    def test_student_display_name_fallback(self):
        s1 = Student(legal_first_name="Alexandra", last_name="Smith")
        self.assertEqual(s1.display_name, "Alexandra Smith")

        s2 = Student(
            legal_first_name="Alexandra", preferred_first_name="Alex", last_name="Smith"
        )
        self.assertEqual(s2.display_name, "Alex Smith")
        self.assertEqual(s2.full_name, "Alex Smith")


class DemographicModelTests(TestCase):
    def test_race_ethnicity_match_from_text(self):
        keys = [
            ("black-or-african-american", "Black or African-American"),
            ("hispanic-or-latino", "Hispanic or Latino"),
            ("asian", "Asian"),
            ("white", "White"),
            ("other", "Other"),
        ]
        for k, n in keys:
            RaceEthnicity.objects.get_or_create(key=k, defaults={"name": n})
        qs = RaceEthnicity.match_from_text("Black, Hispanic and Asian")
        self.assertSetEqual(
            set(qs.values_list("key", flat=True)),
            {"black-or-african-american", "hispanic-or-latino", "asian"},
        )
        qs2 = RaceEthnicity.match_from_text("Something totally unknown")
        self.assertIn("other", set(qs2.values_list("key", flat=True)))


class UnsavedStudentModelTests(TestCase):
    def test_all_parents_on_unsaved_student(self):
        student = Student(legal_first_name="Unsaved", last_name="Student")
        # Accessing all_parents should not raise ValueError
        try:
            parents = student.all_parents
            self.assertEqual(parents, [])
        except ValueError as e:
            self.fail(f"all_parents() raised ValueError on unsaved student: {e}")
