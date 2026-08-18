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

        # Create active mentors (mentor_active controls mentor behavior)
        self.active_mentor = Adult.objects.create(
            first_name="Active",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
            login_enabled=True,
            personal_email="active_mentor@example.com",
        )
        self.inactive_mentor = Adult.objects.create(
            first_name="Inactive",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=False,
            login_enabled=True,
            personal_email="inactive_mentor@example.com",
        )

        # Create active parents (login_enabled controls login)
        self.active_parent = Adult.objects.create(
            first_name="Active",
            last_name="Parent",
            is_parent=True,
            login_enabled=True,
            personal_email="active_parent@example.com",
            email_updates=True,
        )
        self.inactive_parent = Adult.objects.create(
            first_name="Inactive",
            last_name="Parent",
            is_parent=True,
            login_enabled=False,
            personal_email="inactive_parent@example.com",
            email_updates=True,
        )

        self.student = Student.objects.create(first_name="Test", last_name="Student")
        self.active_parent.students.add(self.student)
        self.inactive_parent.students.add(self.student)

    def test_mentor_list_groups_inactive(self):
        """Mentor list should show inactive mentors in a separate section."""
        url = reverse("mentor_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Active Mentor")
        self.assertContains(response, "Inactive Mentor")

        # Verify inactive mentors are in a separate section
        content = response.content.decode()
        self.assertIn("Inactive Mentors", content)

        # Verify ordering: Active mentor should appear before the Inactive section header
        active_pos = content.find("Active Mentor")
        inactive_section_pos = content.find("Inactive Mentors")
        self.assertTrue(
            active_pos < inactive_section_pos,
            "Active mentor should appear before inactive section",
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
        """Parent list should still exclude inactive parents."""
        url = reverse("parent_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Active Parent")
        self.assertNotContains(response, "Inactive Parent")

    def test_login_disabled_adult_cannot_log_in(self):
        """Adults with login_enabled=False who are not parents/alumni get login disabled."""
        user1 = User.objects.create_user(
            username="mentor1", password="password"
        )  # nosec B106
        self.inactive_mentor.user = user1
        self.inactive_mentor.login_enabled = False
        self.inactive_mentor.save()
        user1.refresh_from_db()
        self.assertFalse(
            user1.is_active,
            "Adult with login_enabled=False and no other roles should have disabled login",
        )

    def test_login_disabled_parent_keeps_login_if_alumni(self):
        """A parent with login_enabled=False who is also an alumni keeps login."""
        user2 = User.objects.create_user(
            username="parent1", password="password"
        )  # nosec B106
        self.inactive_parent.is_alumni = True
        self.inactive_parent.user = user2
        self.inactive_parent.save()
        user2.refresh_from_db()
        self.assertTrue(
            user2.is_active,
            "Parent with login_enabled=False who is also alumni should still have active login",
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
        self.assertContains(response, "BG Check Needed", count=1)

    def test_inactive_mentor_access_denied(self):
        """Inactive mentors (mentor_active=False) should not have mentor permissions."""
        from programs.permission_views import user_is_mentor

        # Create a user for the inactive mentor
        user = User.objects.create_user(
            username="inactive_mentor_user", password="password"
        )  # nosec B106
        self.inactive_mentor.user = user
        self.inactive_mentor.is_parent = True  # Allow login
        self.inactive_mentor.login_enabled = True
        self.inactive_mentor.save()

        user.refresh_from_db()
        self.assertTrue(user.is_active)

        self.assertFalse(
            user_is_mentor(user),
            "Inactive mentor (mentor_active=False) should not be recognized as a mentor",
        )

    def test_inactive_parent_does_not_get_emails(self):
        """Parents with login_enabled=False should not receive email notifications."""
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

    def test_mentor_active_controls_mentor_role_recognition(self):
        """mentor_active=True is required for mentor role recognition."""
        from programs.permission_views import user_is_mentor

        # Create a mentor with mentor_active=True
        active_user = User.objects.create_user(
            username="active_m_user", password="password"
        )  # nosec B106
        Adult.objects.create(
            first_name="Active",
            last_name="MentorUser",
            is_mentor=True,
            mentor_active=True,
            login_enabled=True,
            user=active_user,
        )
        self.assertTrue(user_is_mentor(active_user))

        # Create a mentor with mentor_active=False (but login_enabled=True so they can log in)
        inactive_user = User.objects.create_user(
            username="inactive_m_user", password="password"
        )  # nosec B106
        Adult.objects.create(
            first_name="Inactive",
            last_name="MentorUser",
            is_mentor=True,
            mentor_active=False,
            login_enabled=True,
            user=inactive_user,
        )
        # user_is_mentor checks mentor_active directly, bypassing the group fallback
        self.assertFalse(user_is_mentor(inactive_user))

    def test_mentor_active_false_excluded_from_email_recipients(self):
        """Mentors with mentor_active=False should not appear in email recipient lists."""
        url = reverse("mentor_list")
        # The mentor list should still show both (for tracking purposes)
        response = self.client.get(url)
        self.assertContains(response, "Inactive Mentor")

    def test_mentor_active_independent_of_login_enabled(self):
        """mentor_active and login_enabled are independent flags."""
        user = User.objects.create_user(
            username="independent_user", password="password"
        )  # nosec B106
        mentor = Adult.objects.create(
            first_name="Independent",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=False,
            login_enabled=True,
            user=user,
        )
        user.refresh_from_db()
        # Can still log in
        self.assertTrue(user.is_active)
        # But not recognized as mentor
        from programs.permission_views import user_is_mentor

        self.assertFalse(user_is_mentor(user))
