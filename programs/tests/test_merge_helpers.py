"""Tests for shared merge helper utilities."""

from django.test import TestCase
from django.utils import timezone

from attendance.models import AttendanceEvent, AttendanceSession
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Program,
    School,
    Student,
)
from programs.utils.merges import (
    carry_over_missing_fields,
    perform_parent_merge,
    perform_school_merge,
    perform_student_merge,
    perform_visitor_merge,
)


class MergeHelpersTests(TestCase):
    def test_carry_over_missing_fields(self):
        source = Student(
            legal_first_name="Jane",
            last_name="Doe",
            personal_email="jane@example.com",
            address="123 Main St",
            can_receive_texts=True,
        )
        keep = Student(
            legal_first_name="Jane",
            last_name="Doe",
            personal_email="",
            address="",
            can_receive_texts=False,
        )

        changed = carry_over_missing_fields(keep, source)
        self.assertTrue(changed)
        self.assertEqual(keep.personal_email, "jane@example.com")
        self.assertEqual(keep.address, "123 Main St")
        self.assertTrue(keep.can_receive_texts)

    def test_perform_school_merge(self):
        s_keep = School.objects.create(
            name="Central High", city="Pittsburgh", state="PA"
        )
        s_source = School.objects.create(
            name="Old Central High", street_address="500 5th Ave", state="PA"
        )
        student = Student.objects.create(
            legal_first_name="Sam", last_name="Smith", school=s_source
        )
        source_pk = s_source.pk

        meta = perform_school_merge(s_keep, s_source)
        self.assertEqual(meta["source_pk"], source_pk)
        self.assertEqual(meta["source_name"], "Old Central High")
        self.assertEqual(meta["keep_name"], "Central High")

        s_keep.refresh_from_db()
        self.assertEqual(s_keep.street_address, "500 5th Ave")
        student.refresh_from_db()
        self.assertEqual(student.school, s_keep)
        self.assertFalse(School.objects.filter(pk=source_pk).exists())

    def test_perform_parent_merge(self):
        p_keep = Adult.objects.create(
            legal_first_name="Mary", last_name="Johnson", is_parent=True
        )
        p_source = Adult.objects.create(
            legal_first_name="Mary",
            last_name="Johnson",
            personal_email="mary@example.com",
            phone_number="412-555-1234",
            is_parent=True,
            is_mentor=True,
        )
        student = Student.objects.create(legal_first_name="Kid", last_name="Johnson")
        AdultStudentRelationship.objects.create(
            adult=p_source, student=student, specific_relationship="Mother"
        )
        source_pk = p_source.pk

        meta = perform_parent_merge(p_keep, p_source)
        self.assertEqual(meta["source_pk"], source_pk)

        p_keep.refresh_from_db()
        self.assertEqual(p_keep.personal_email, "mary@example.com")
        self.assertEqual(p_keep.phone_number, "412-555-1234")
        self.assertTrue(p_keep.is_mentor)
        self.assertTrue(
            AdultStudentRelationship.objects.filter(
                adult=p_keep, student=student
            ).exists()
        )
        self.assertFalse(Adult.objects.filter(pk=source_pk).exists())

    def test_perform_student_merge(self):
        prog = Program.objects.create(name="Robotics 2026")
        st_keep = Student.objects.create(legal_first_name="Alex", last_name="Taylor")
        st_source = Student.objects.create(
            legal_first_name="Alex",
            last_name="Taylor",
            personal_email="alex@example.com",
            phone_number="412-555-9876",
        )
        Enrollment.objects.create(student=st_source, program=prog)
        source_pk = st_source.pk

        meta = perform_student_merge(st_keep, st_source)
        self.assertEqual(meta["source_pk"], source_pk)

        st_keep.refresh_from_db()
        self.assertEqual(st_keep.personal_email, "alex@example.com")
        self.assertEqual(st_keep.phone_number, "412-555-9876")
        self.assertTrue(
            Enrollment.objects.filter(student=st_keep, program=prog).exists()
        )
        self.assertFalse(Student.objects.filter(pk=source_pk).exists())

    def test_perform_visitor_merge(self):
        prog = Program.objects.create(name="Robotics 2026")
        now = timezone.now()
        AttendanceSession.objects.create(
            program=prog, visitor_name="John D.", check_in=now
        )
        AttendanceSession.objects.create(
            program=prog, visitor_name="John Doe", check_in=now
        )
        AttendanceEvent.objects.create(
            program=prog, visitor_name="John D.", occurred_at=now
        )

        count = perform_visitor_merge(["John D."], "John Doe")
        self.assertGreaterEqual(count, 2)

        self.assertFalse(
            AttendanceSession.objects.filter(visitor_name="John D.").exists()
        )
        self.assertEqual(
            AttendanceSession.objects.filter(visitor_name="John Doe").count(), 2
        )
        self.assertFalse(
            AttendanceEvent.objects.filter(visitor_name="John D.").exists()
        )
        self.assertEqual(
            AttendanceEvent.objects.filter(visitor_name="John Doe").count(), 1
        )
