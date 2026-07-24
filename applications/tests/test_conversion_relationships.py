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
        self.assertEqual(student.primary_contact.personal_email, "pat@example.com")
        self.assertEqual(student.secondary_contact.personal_email, "sam@example.com")

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
            personal_email="pat@example.com",
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
        self.assertEqual(
            Adult.objects.filter(personal_email="pat@example.com").count(), 1
        )

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
            personal_email="pat@example.com",
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
        self.assertEqual(
            Adult.objects.filter(personal_email="pat@example.com").count(), 1
        )

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
        self.assertEqual(
            Adult.objects.filter(personal_email="pat@example.com").count(), 1
        )

        # Bi-directional checks for Student A
        self.assertIn(student_a, parent_a.primary_for.all())
        self.assertIn(student_a, parent_a.students.all())

        # Bi-directional checks for Student B
        self.assertIn(student_b, parent_a.primary_for.all())
        self.assertIn(student_b, parent_a.students.all())

    def test_two_parents_same_email_different_names(self):
        """
        When a mother and father share the same email address, conversion
        must create two separate Adult records (matched by email + name),
        not collapse them into one.
        """
        data = {
            "step5-student": {
                "legal_first_name": "Ada",
                "last_name": "Lovelace",
                "personal_email": "ada@example.com",
                "date_of_birth": "2010-01-01",
            },
            "step7-primaryparent": {
                "first_name": "Mary",
                "last_name": "Smith",
                "email": "shared@example.com",
            },
            "step8-secondaryparent": {
                "first_name": "John",
                "last_name": "Smith",
                "email": "shared@example.com",
            },
        }
        app = self._create_app(data)
        student = convert_application_to_student(app)

        primary = student.primary_contact
        secondary = student.secondary_contact

        # Both contacts must exist and be distinct records
        self.assertIsNotNone(primary)
        self.assertIsNotNone(secondary)
        self.assertNotEqual(primary.pk, secondary.pk)

        # Names must be preserved correctly
        self.assertEqual(primary.first_name, "Mary")
        self.assertEqual(secondary.first_name, "John")

        # Both should be linked to the student
        self.assertIn(student, primary.primary_for.all())
        self.assertIn(student, secondary.secondary_for.all())

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


class DuplicateApplicationConversionTests(TestCase):
    """
    Verify that converting one application does not corrupt or break other
    pending/incomplete applications that share the same student or parent email.
    """

    def setUp(self):
        self.program = Program.objects.create(name="Test Program")

    def _make_app(self, student_email, parent_email, student_first, status=None):
        return Application.objects.create(
            program=self.program,
            email=student_email,
            status=status or Application.Status.APPROVED_SIGNED,
            data={
                "step5-student": {
                    "legal_first_name": student_first,
                    "last_name": "Doe",
                    "personal_email": student_email,
                    "date_of_birth": "2010-06-15",
                },
                "step7-primaryparent": {
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "email": parent_email,
                },
            },
        )

    def test_converting_one_app_leaves_sibling_app_intact(self):
        """
        Two applications share the same student email and parent email.
        Converting the first must not alter the second application's data,
        status, or email fields.
        """
        app1 = self._make_app("student@example.com", "parent@example.com", "Alice")
        app2 = self._make_app("student@example.com", "parent@example.com", "Alice")

        # Convert only app1
        convert_application_to_student(app1)

        # app2 must be completely untouched
        app2.refresh_from_db()
        self.assertEqual(app2.status, Application.Status.APPROVED_SIGNED)
        self.assertEqual(app2.email, "student@example.com")
        self.assertIsNone(app2.converted_student_id)

    def test_second_app_still_converts_correctly_after_first(self):
        """
        After converting app1, converting app2 (same student email) must
        succeed and reuse the already-created Student and Adult records
        rather than creating duplicates.
        """
        app1 = self._make_app("student@example.com", "parent@example.com", "Alice")
        app2 = self._make_app("student@example.com", "parent@example.com", "Alice")

        student1 = convert_application_to_student(app1)
        student2 = convert_application_to_student(app2)

        # Same student record reused (idempotent)
        self.assertEqual(student1.pk, student2.pk)

        # No duplicate Adults created
        self.assertEqual(
            Adult.objects.filter(personal_email="parent@example.com").count(), 1
        )
        # No duplicate Students created
        self.assertEqual(
            Student.objects.filter(personal_email="student@example.com").count(), 1
        )

    def test_incomplete_app_unaffected_when_approved_sibling_converted(self):
        """
        An incomplete (IN_PROGRESS) application sharing the same parent email
        must remain untouched after a different approved application is converted.
        """
        approved_app = self._make_app(
            "student_a@example.com", "parent@example.com", "Alice"
        )
        incomplete_app = Application.objects.create(
            program=self.program,
            email="student_b@example.com",
            status=Application.Status.DRAFT,
            data={
                "step7-primaryparent": {
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "email": "parent@example.com",
                },
            },
        )

        convert_application_to_student(approved_app)

        incomplete_app.refresh_from_db()
        self.assertEqual(incomplete_app.status, Application.Status.DRAFT)
        self.assertIsNone(incomplete_app.converted_student_id)
        # Data blob must be unchanged
        self.assertEqual(
            incomplete_app.data["step7-primaryparent"]["email"], "parent@example.com"
        )

    def test_two_apps_same_parent_email_different_students_both_convert(self):
        """
        Two different students whose parent shares the same email address.
        Both applications must convert successfully, reusing the single
        Adult record for the parent and linking it to both students.
        """
        app1 = self._make_app("alice@example.com", "parent@example.com", "Alice")
        app2 = self._make_app("bob@example.com", "parent@example.com", "Bob")
        # Give app2 a distinct student name so a new Student is created
        app2.data["step5-student"]["legal_first_name"] = "Bob"
        app2.data["step5-student"]["personal_email"] = "bob@example.com"
        app2.save()

        student_a = convert_application_to_student(app1)
        student_b = convert_application_to_student(app2)

        self.assertNotEqual(student_a.pk, student_b.pk)

        # Only one Adult for the shared parent email
        self.assertEqual(
            Adult.objects.filter(personal_email="parent@example.com").count(), 1
        )
        parent = Adult.objects.get(personal_email="parent@example.com")
        self.assertIn(student_a, parent.primary_for.all())
        self.assertIn(student_b, parent.primary_for.all())
