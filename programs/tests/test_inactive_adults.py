from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from programs.models import Adult, Enrollment, Program, Student


class InactiveAdultTest(TestCase):
    def setUp(self):
        # Create a Lead Mentor to access lists
        self.lead_mentor_user = User.objects.create_superuser(
            username="leadmentor", email="lead@example.com", password="password"
        )  # nosec B106
        self.lead_mentor_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor_user.groups.add(self.lead_mentor_group)

        self.client = Client()
        self.client.login(username="leadmentor", password="password")  # nosec B106

        # Create active and inactive adults
        self.active_mentor = Adult.objects.create(
            first_name="Active",
            last_name="Mentor",
            is_mentor=True,
            active=True,
            personal_email="active_mentor@example.com",
        )
        self.inactive_mentor = Adult.objects.create(
            first_name="Inactive",
            last_name="Mentor",
            is_mentor=True,
            active=False,
            personal_email="inactive_mentor@example.com",
        )

        self.active_parent = Adult.objects.create(
            first_name="Active",
            last_name="Parent",
            is_parent=True,
            active=True,
            personal_email="active_parent@example.com",
            email_updates=True,
        )
        self.inactive_parent = Adult.objects.create(
            first_name="Inactive",
            last_name="Parent",
            is_parent=True,
            active=False,
            personal_email="inactive_parent@example.com",
            email_updates=True,
        )

        self.student = Student.objects.create(first_name="Test", last_name="Student")
        self.active_parent.students.add(self.student)
        self.inactive_parent.students.add(self.student)

    def test_mentor_list_includes_inactive_at_bottom(self):
        """Mentor list should show inactive mentors at the bottom."""
        url = reverse("mentor_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Active Mentor")
        self.assertContains(response, "Inactive Mentor")

        # Verify ordering: Active should appear before Inactive
        content = response.content.decode()
        active_pos = content.find("Active Mentor")
        inactive_pos = content.find("Inactive Mentor")
        self.assertTrue(
            active_pos < inactive_pos,
            "Active mentor should appear before inactive mentor",
        )

    def test_adult_list_includes_inactive_at_bottom(self):
        """Adult list should show all adults, inactive at the bottom."""
        url = reverse("adult_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Active Mentor")
        self.assertContains(response, "Inactive Parent")

        # Verify ordering
        content = response.content.decode()
        active_pos = content.find("Active Mentor")
        inactive_pos = content.find("Inactive Parent")
        self.assertTrue(
            active_pos < inactive_pos,
            "Active adult should appear before inactive adult",
        )

    def test_parent_list_still_excludes_inactive(self):
        """Parent list should still exclude inactive parents (as they were not explicitly changed)."""
        url = reverse("parent_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Active Parent")
        self.assertNotContains(response, "Inactive Parent")

    def test_login_logic_for_inactive_adults(self):
        """Inactive adults can only log in if they are also parents or alumni."""
        # 1. Inactive mentor who is NOT a parent/alumni
        user1 = User.objects.create_user(
            username="mentor1", password="password"
        )  # nosec B106
        self.inactive_mentor.user = user1
        self.inactive_mentor.save()  # This triggers User sync
        user1.refresh_from_db()
        self.assertFalse(
            user1.is_active,
            "Inactive mentor without other roles should have disabled login",
        )

        # 2. Inactive mentor who IS a parent
        user2 = User.objects.create_user(
            username="parent1", password="password"
        )  # nosec B106
        self.inactive_parent.is_mentor = True  # Make them a mentor too
        self.inactive_parent.user = user2
        self.inactive_parent.save()
        user2.refresh_from_db()
        self.assertTrue(
            user2.is_active,
            "Inactive mentor who is also a parent should still have active login",
        )

    def test_background_check_badges(self):
        """Mentors missing clearances should show a badge."""
        url = reverse("mentor_list")

        # Active mentor needs background check (is_mentor=True)
        self.assertTrue(self.active_mentor.needs_background_check())

        response = self.client.get(url)
        self.assertContains(response, "BG Check Needed")

        # Add clearances for active mentor
        from programs.models import BackgroundCheck, BackgroundCheckType

        for check_type in BackgroundCheckType.values:
            BackgroundCheck.objects.create(
                adult=self.active_mentor,
                check_type=check_type,
                cleared=True,
                obtained_date=timezone.now().date(),
            )

        self.assertFalse(self.active_mentor.needs_background_check())

        response = self.client.get(url)
        # Count "BG Check Needed" - should still be there for Inactive Mentor if not cleared
        # but self.active_mentor should no longer have it.
        # self.inactive_mentor is also a mentor and hasn't been cleared.
        self.assertContains(response, "BG Check Needed", count=1)

    def test_inactive_mentor_access_denied(self):
        """Inactive mentors should not have mentor permissions."""
        from programs.permission_views import user_is_mentor

        # Create a user for the inactive mentor
        user = User.objects.create_user(
            username="inactive_mentor_user", password="password"
        )  # nosec B106
        self.inactive_mentor.user = user
        self.inactive_mentor.is_parent = True  # Allow login
        self.inactive_mentor.save()

        user.refresh_from_db()
        self.assertTrue(user.is_active)

        self.assertFalse(
            user_is_mentor(user), "Inactive mentor should not be recognized as a mentor"
        )

    def test_inactive_parent_does_not_get_emails(self):
        """Inactive parents should not be included in recipient lists for notifications."""
        from django.core import mail

        from programs.models import Fee
        from programs.signals import _send_fee_notification

        # Create a fee to trigger notification
        program = Program.objects.create(name="Test Program", active=True)
        fee = Fee.objects.create(program=program, name="Test Fee", amount=100)

        # Clear any existing mail
        mail.outbox = []

        # Trigger notification manually through the signal helper
        _send_fee_notification(self.student, program, fee)

        # Check that only the active parent got the email
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["active_parent@example.com"])
        self.assertNotIn("inactive_parent@example.com", mail.outbox[0].to)
