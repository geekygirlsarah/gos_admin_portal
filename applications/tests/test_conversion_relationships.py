from django.test import TestCase

from applications.models import Application
from applications.services import convert_application_to_student
from programs.models import Adult, Program, Student


class ConversionRelationshipTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(name="Test Program")

    def _create_app(self, data):
        return Application.objects.create(
            program=self.program,
            email="student@example.com",
            status=Application.Status.APPROVED_SIGNED,
            data=data,
        )

    def test_bidirectional_relationship_new_parents(self):
        """
        Test that conversion creates a bi-directional relationship.
        Specifically, check if primary/secondary contacts are set AND
        added to the ManyToMany 'adults' relationship.
        """
        data = {
            "step5-student": {
                "legal_first_name": "Ada",
                "last_name": "Lovelace",
                "personal_email": "ada@example.com",
                "date_of_birth": "2010-01-01",
            },
            "step7-primaryparent": {
                "first_name": "Pat",
                "last_name": "Parent",
                "email": "pat@example.com",
            },
            "step8-secondaryparent": {
                "first_name": "Sam",
                "last_name": "Parent",
                "email": "sam@example.com",
            },
        }
        app = self._create_app(data)

        student = convert_application_to_student(app)

        # Check FKs
        self.assertEqual(student.primary_contact.email, "pat@example.com")
        self.assertEqual(student.secondary_contact.email, "sam@example.com")

        # Check bi-directional via FK related names
        self.assertIn(student, student.primary_contact.primary_for.all())
        self.assertIn(student, student.secondary_contact.secondary_for.all())

        # Check ManyToMany (the user likely expects this to be populated too)
        # Note: Student.parents is an alias for Student.adults
        self.assertIn(
            student.primary_contact,
            student.adults.all(),
            "Primary contact should be in student.adults M2M",
        )
        self.assertIn(
            student.secondary_contact,
            student.adults.all(),
            "Secondary contact should be in student.adults M2M",
        )

        # Check from Adult side
        self.assertIn(
            student,
            student.primary_contact.students.all(),
            "Student should be in primary contact's students M2M",
        )
        self.assertIn(
            student,
            student.secondary_contact.students.all(),
            "Student should be in secondary contact's students M2M",
        )

    def test_reuse_existing_parents(self):
        """
        Test that conversion reuses existing adults matched by email.
        """
        existing_parent = Adult.objects.create(
            first_name="Pat",
            last_name="Parent",
            email="pat@example.com",
            is_parent=True,
        )

        data = {
            "step5-student": {
                "legal_first_name": "Ada",
                "last_name": "Lovelace",
                "personal_email": "ada@example.com",
                "date_of_birth": "2010-01-01",
            },
            "step7-primaryparent": {
                "first_name": "Pat",
                "last_name": "Parent",
                "email": "pat@example.com",
            },
        }
        app = self._create_app(data)

        student = convert_application_to_student(app)

        self.assertEqual(student.primary_contact, existing_parent)
        self.assertEqual(Adult.objects.filter(email="pat@example.com").count(), 1)

        # Verify relationship
        self.assertIn(student, existing_parent.primary_for.all())
        # Check M2M too
        self.assertIn(existing_parent, student.adults.all())
        self.assertIn(student, existing_parent.students.all())

    def test_existing_parent_with_other_student(self):
        """
        Test that an existing parent (linked to another student) is correctly
        linked to the new student without duplicates or breaking old link.
        """
        existing_parent = Adult.objects.create(
            first_name="Pat",
            last_name="Parent",
            email="pat@example.com",
            is_parent=True,
        )
        other_student = Student.objects.create(
            legal_first_name="Old",
            last_name="Student",
            primary_contact=existing_parent,
            date_of_birth="2010-01-01",
        )
        # Manually add to M2M as well if that's expected
        existing_parent.students.add(other_student)

        data = {
            "step5-student": {
                "legal_first_name": "Ada",
                "last_name": "Lovelace",
                "personal_email": "ada@example.com",
                "date_of_birth": "2010-01-01",
            },
            "step7-primaryparent": {
                "first_name": "Pat",
                "last_name": "Parent",
                "email": "pat@example.com",
            },
        }
        app = self._create_app(data)

        student = convert_application_to_student(app)

        self.assertEqual(student.primary_contact, existing_parent)
        self.assertEqual(Adult.objects.filter(email="pat@example.com").count(), 1)

        # Verify relationships for NEW student
        self.assertIn(student, existing_parent.primary_for.all())
        self.assertIn(student, existing_parent.students.all())

        # Verify relationships for OLD student are preserved
        self.assertIn(other_student, existing_parent.primary_for.all())
        self.assertIn(other_student, existing_parent.students.all())

    def test_parent_signing_up_two_students_sequential(self):
        """
        Test that if a parent signs up two different students, they both end
        up linked to the same parent record, and all relationships are correct.
        """
        parent_data = {
            "first_name": "Pat",
            "last_name": "Parent",
            "email": "pat@example.com",
        }

        # Application 1 for Student A
        app1 = self._create_app(
            {
                "step5-student": {
                    "legal_first_name": "Ada",
                    "last_name": "Lovelace",
                    "date_of_birth": "2010-01-01",
                },
                "step7-primaryparent": parent_data,
            }
        )

        # Application 2 for Student B
        app2 = self._create_app(
            {
                "step5-student": {
                    "legal_first_name": "Barbara",
                    "last_name": "Liskov",
                    "date_of_birth": "2010-01-01",
                },
                "step7-primaryparent": parent_data,
            }
        )

        # Convert Student A
        student_a = convert_application_to_student(app1)
        parent_a = student_a.primary_contact

        # Convert Student B
        student_b = convert_application_to_student(app2)
        parent_b = student_b.primary_contact

        # They should be the same record
        self.assertEqual(parent_a, parent_b)
        self.assertEqual(Adult.objects.filter(email="pat@example.com").count(), 1)

        # Bi-directional checks for Student A
        self.assertIn(student_a, parent_a.primary_for.all())
        self.assertIn(student_a, parent_a.students.all())

        # Bi-directional checks for Student B
        self.assertIn(student_b, parent_a.primary_for.all())
        self.assertIn(student_b, parent_a.students.all())

    def test_parent_signing_up_two_students_different_programs(self):
        """
        Test that parent reuse works correctly across different programs.
        """
        other_program = Program.objects.create(name="Other Program")
        parent_data = {
            "first_name": "Pat",
            "last_name": "Parent",
            "email": "pat@example.com",
        }

        # App 1 in Program 1
        app1 = self._create_app(
            {
                "step5-student": {
                    "legal_first_name": "Ada",
                    "last_name": "Lovelace",
                    "date_of_birth": "2010-01-01",
                },
                "step7-primaryparent": parent_data,
            }
        )

        # App 2 in Program 2
        app2 = Application.objects.create(
            program=other_program,
            email="student_b@example.com",
            status=Application.Status.APPROVED_SIGNED,
            data={
                "step5-student": {
                    "legal_first_name": "Barbara",
                    "last_name": "Liskov",
                    "date_of_birth": "2010-01-01",
                },
                "step7-primaryparent": parent_data,
            },
        )

        # Convert both
        student_a = convert_application_to_student(app1)
        student_b = convert_application_to_student(app2)

        # Verify parent reuse
        self.assertEqual(student_a.primary_contact, student_b.primary_contact)
        parent = student_a.primary_contact

        # Verify bi-directional
        self.assertCountEqual(parent.students.all(), [student_a, student_b])
        self.assertIn(student_a, parent.primary_for.all())
        self.assertIn(student_b, parent.primary_for.all())
