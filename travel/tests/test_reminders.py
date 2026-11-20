from datetime import datetime, timedelta
from datetime import timezone as datetime_timezone
from threading import Barrier, Thread
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from organizations.models import Organization
from programs.models import Adult, AdultStudentRelationship, Program, Student
from travel.models import (
    TravelDeparture,
    TravelEvent,
    TravelReminderDelivery,
    TravelSignup,
)
from travel.reminders import _claim_plan, run_travel_reminder_batch


class TravelReminderTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            name="Travel Reminder Tests",
            slug="travel-reminder-tests",
            subdomain="travel-reminder-tests",
            settings={"time_zone": "America/Los_Angeles"},
        )
        self.foreign_organization = Organization.objects.create(
            name="Foreign Travel Reminder Tests",
            slug="foreign-travel-reminder-tests",
            subdomain="foreign-travel-reminder-tests",
        )
        self.program = Program.objects.create(
            organization=self.organization,
            name="Reminder Program",
        )
        self.student_user = User.objects.create_user(
            username="travel-reminder-student",
            email="student@example.test",
        )
        self.student = Student.objects.create(
            organization=self.organization,
            user=self.student_user,
            legal_first_name="Reminder",
            last_name="Student",
        )
        self.guardian = Adult.objects.create(
            organization=self.organization,
            is_parent=True,
            login_enabled=True,
            email_updates=True,
            personal_email="guardian@example.test",
        )
        AdultStudentRelationship.objects.create(
            organization=self.organization,
            adult=self.guardian,
            student=self.student,
        )
        self.event = TravelEvent.objects.create(
            organization=self.organization,
            program=self.program,
            name="Reminder Competition",
            start_date=timezone.localdate() + timedelta(days=30),
            end_date=timezone.localdate() + timedelta(days=31),
            permission_form="travel/forms/reminder-test.pdf",
        )
        mail.outbox.clear()

    def make_signup(self, *, status=TravelSignup.INTERESTED, event=None, student=None):
        return TravelSignup.objects.create(
            organization=self.organization,
            student=student or self.student,
            event=event or self.event,
            status=status,
        )

    def test_three_day_after_signup_reminder_claims_once_for_family(self):
        now = timezone.now()
        signup = self.make_signup()
        TravelSignup.objects.filter(pk=signup.pk).update(
            created_at=now - timedelta(days=3)
        )

        summary = run_travel_reminder_batch(self.organization, now=now)

        self.assertEqual(summary["sent"], 1)
        self.assertEqual(
            {message.to[0] for message in mail.outbox},
            {"student@example.test", "guardian@example.test"},
        )
        self.assertEqual(
            list(
                TravelReminderDelivery.objects.for_organization(self.organization)
                .filter(signup=signup)
                .values_list("reminder_type", flat=True)
            ),
            [TravelReminderDelivery.ReminderType.AFTER_SIGNUP],
        )

        second_run = run_travel_reminder_batch(self.organization, now=now)

        self.assertEqual(second_run["sent"], 0)
        self.assertEqual(len(mail.outbox), 2)

    def test_seven_and_one_day_reminders_use_selected_travel_date(self):
        today = timezone.localdate()
        signup = self.make_signup(status=TravelSignup.APPROVED)
        self.event.start_date = today + timedelta(days=7)
        self.event.end_date = today + timedelta(days=8)
        self.event.requires_departure = True
        self.event.save(
            update_fields=["start_date", "end_date", "requires_departure", "updated_at"]
        )

        departure = TravelDeparture.objects.create(
            organization=self.organization,
            event=self.event,
            label="Late departure",
            leave_date=today + timedelta(days=7),
        )
        signup.departure = departure
        signup.save(update_fields=["departure", "updated_at"])
        first_now = datetime.combine(
            today, datetime.min.time(), tzinfo=ZoneInfo("America/Los_Angeles")
        )

        first_summary = run_travel_reminder_batch(
            self.organization,
            now=first_now,
        )
        self.assertEqual(first_summary["sent"], 1)
        self.assertEqual(
            TravelReminderDelivery.objects.for_organization(self.organization)
            .filter(signup=signup)
            .get()
            .reminder_type,
            TravelReminderDelivery.ReminderType.SEVEN_DAYS,
        )

        second_now = first_now + timedelta(days=6)
        second_summary = run_travel_reminder_batch(
            self.organization,
            now=second_now,
        )
        self.assertEqual(second_summary["sent"], 1)
        self.assertSetEqual(
            set(
                TravelReminderDelivery.objects.for_organization(self.organization)
                .filter(signup=signup)
                .values_list("reminder_type", flat=True)
            ),
            {
                TravelReminderDelivery.ReminderType.SEVEN_DAYS,
                TravelReminderDelivery.ReminderType.ONE_DAY,
            },
        )

    def test_organization_local_date_controls_reminder_window(self):
        organization = self.organization
        signup = self.make_signup(status=TravelSignup.APPROVED)
        event_date = datetime(2026, 11, 27).date()
        self.event.start_date = event_date
        self.event.end_date = event_date
        self.event.save(update_fields=["start_date", "end_date", "updated_at"])
        now = datetime(2026, 11, 20, 8, 30, tzinfo=datetime_timezone.utc)

        summary = run_travel_reminder_batch(organization, now=now, dry_run=True)

        self.assertEqual(summary["planned"], 1)
        self.assertFalse(
            TravelReminderDelivery.objects.for_organization(organization)
            .filter(signup=signup)
            .exists()
        )

    def test_missing_eligible_contacts_are_recorded_without_sending_or_retrying(self):
        self.student_user.email = ""
        self.student_user.save(update_fields=["email"])
        self.guardian.email_updates = False
        self.guardian.save(update_fields=["email_updates"])
        signup = self.make_signup()
        now = timezone.now()
        TravelSignup.objects.filter(pk=signup.pk).update(
            created_at=now - timedelta(days=3)
        )

        summary = run_travel_reminder_batch(self.organization, now=now)

        self.assertEqual(summary["no_recipients"], 1)
        delivery = TravelReminderDelivery.objects.for_organization(
            self.organization
        ).get(signup=signup)
        self.assertEqual(delivery.status, TravelReminderDelivery.Status.NO_RECIPIENTS)
        self.assertEqual(delivery.recipient_count, 0)
        self.assertEqual(mail.outbox, [])

    def test_uncertain_recipient_failure_continues_and_is_not_retried(self):
        signup = self.make_signup()
        now = timezone.now()
        TravelSignup.objects.filter(pk=signup.pk).update(
            created_at=now - timedelta(days=3)
        )
        with patch(
            "travel.services.send_templated_notification",
            side_effect=[RuntimeError("ambiguous send"), None],
        ) as send_notice:
            summary = run_travel_reminder_batch(self.organization, now=now)
            second_summary = run_travel_reminder_batch(self.organization, now=now)

        self.assertEqual(summary["unknown"], 1)
        self.assertEqual(second_summary["sent"], 0)
        self.assertEqual(send_notice.call_count, 2)
        delivery = TravelReminderDelivery.objects.for_organization(
            self.organization
        ).get(signup=signup)
        self.assertEqual(delivery.status, TravelReminderDelivery.Status.UNKNOWN)
        self.assertEqual(delivery.recipient_count, 2)
        self.assertEqual(delivery.accepted_count, 1)

    def test_foreign_student_signup_is_not_notified(self):
        foreign_student = Student.objects.create(
            organization=self.foreign_organization,
            legal_first_name="Foreign",
            last_name="Student",
        )
        signup = self.make_signup()
        TravelSignup.objects.filter(pk=signup.pk).update(student=foreign_student)
        now = timezone.now()
        TravelSignup.objects.filter(pk=signup.pk).update(
            created_at=now - timedelta(days=3)
        )

        summary = run_travel_reminder_batch(self.organization, now=now)

        self.assertEqual(summary["sent"], 0)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(
            TravelReminderDelivery.objects.for_organization(self.organization)
            .filter(signup=signup)
            .exists()
        )

    def test_management_command_dry_run_does_not_claim_or_send(self):
        signup = self.make_signup()
        now = timezone.now()
        TravelSignup.objects.filter(pk=signup.pk).update(
            created_at=now - timedelta(days=3)
        )

        call_command(
            "send_travel_reminders",
            organization=self.organization.slug,
            dry_run=True,
        )

        self.assertEqual(mail.outbox, [])
        self.assertFalse(
            TravelReminderDelivery.objects.for_organization(self.organization)
            .filter(signup=signup)
            .exists()
        )


class TravelReminderConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.organization = Organization.objects.create(
            name="Travel Reminder Lock Test",
            slug="travel-reminder-lock-test",
            subdomain="travel-reminder-lock-test",
            settings={"time_zone": "America/Los_Angeles"},
        )
        program = Program.objects.create(
            organization=self.organization,
            name="Reminder Lock Program",
        )
        user = User.objects.create_user(
            username="travel-reminder-lock-student",
            email="reminder-lock-student@example.test",
        )
        student = Student.objects.create(
            organization=self.organization,
            user=user,
            legal_first_name="Lock",
            last_name="Student",
        )
        event = TravelEvent.objects.create(
            organization=self.organization,
            program=program,
            name="Reminder Lock Event",
            start_date=timezone.localdate() + timedelta(days=30),
            end_date=timezone.localdate() + timedelta(days=31),
            permission_form="travel/forms/reminder-lock-test.pdf",
        )
        self.signup = TravelSignup.objects.create(
            organization=self.organization,
            student=student,
            event=event,
            status=TravelSignup.INTERESTED,
        )
        now = timezone.now()
        TravelSignup.objects.filter(pk=self.signup.pk).update(
            created_at=now - timedelta(days=3)
        )

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_batches_claim_and_send_a_trigger_only_once(self):
        barrier = Barrier(2)
        results = []
        errors = []

        def synchronized_claim(*args, **kwargs):
            barrier.wait(timeout=10)
            return _claim_plan(*args, **kwargs)

        def run_batch():
            close_old_connections()
            try:
                results.append(run_travel_reminder_batch(self.organization))
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        workers = [Thread(target=run_batch) for _ in range(2)]
        with (
            patch("travel.reminders._claim_plan", new=synchronized_claim),
            patch(
                "travel.reminders._send_individually", return_value=(1, 1)
            ) as send_notice,
        ):
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=15)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(result["sent"] for result in results), 1)
        send_notice.assert_called_once()
        self.assertEqual(
            TravelReminderDelivery.objects.for_organization(self.organization)
            .filter(signup=self.signup)
            .count(),
            1,
        )
