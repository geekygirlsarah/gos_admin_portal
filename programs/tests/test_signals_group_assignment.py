from django.contrib.auth.models import User
from django.test import TestCase

from programs.models import Adult, Student


class SignalGroupAssignmentTests(TestCase):
    """Reproduces the bug where the ``post_save`` receivers for ``Adult``
    and ``Student`` never fired because ``sender`` was a lambda instead of
    a lazy string reference. Django does not call callables passed as
    ``sender``, so the signals were silently never connected.
    """

    def test_mentor_adult_added_to_mentor_group_on_save(self):
        user = User.objects.create_user(
            username="mentor1", password="password"
        )  # nosec B106
        adult = Adult.objects.create(
            first_name="Mel",
            last_name="Mentor",
            is_mentor=True,
            user=user,
        )
        adult.save()

        user.refresh_from_db()
        self.assertTrue(user.groups.filter(name="Mentor").exists())

    def test_parent_adult_added_to_parent_group_on_save(self):
        user = User.objects.create_user(
            username="parent1", password="password"
        )  # nosec B106
        adult = Adult.objects.create(
            first_name="Pat",
            last_name="Parent",
            is_parent=True,
            user=user,
        )
        adult.save()

        user.refresh_from_db()
        self.assertTrue(user.groups.filter(name="Parent").exists())

    def test_student_with_user_added_to_student_group_on_save(self):
        user = User.objects.create_user(
            username="student1", password="password"
        )  # nosec B106
        student = Student.objects.create(
            legal_first_name="Sam",
            last_name="Student",
            user=user,
        )
        student.save()

        user.refresh_from_db()
        self.assertTrue(user.groups.filter(name="Student").exists())
