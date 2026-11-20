from datetime import date, timedelta
from decimal import Decimal
from importlib import import_module
from unittest.mock import Mock

from django.apps import apps
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from organizations.models import Organization
from programs.models import Adult, AdultStudentRelationship, Program, School, Student
from travel.models import (
    TravelCostOption,
    TravelDeparture,
    TravelEvent,
    TravelSignedPermissionForm,
    TravelSignup,
)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SignedPermissionReapprovalMigrationTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            name="Travel Migration Organization",
            slug="travel-reapproval-migration",
            subdomain="travel-reapproval-migration",
        )
        self.other_organization = Organization.objects.create(
            name="Other Travel Migration Organization",
            slug="other-travel-reapproval-migration",
            subdomain="other-travel-reapproval-migration",
        )
        self.program = Program.objects.create(
            organization=self.organization, name="Travel Migration Program"
        )
        self.other_program = Program.objects.create(
            organization=self.other_organization, name="Other Travel Migration Program"
        )
        self.school = School.objects.create(
            organization=self.organization, name="Travel Migration School"
        )
        self.parent = Adult.objects.create(
            organization=self.organization,
            is_parent=True,
            login_enabled=True,
            email_updates=True,
            personal_email="parent@example.com",
        )
        self.timestamp = timezone.now()
        self.student_number = 0
        self.schema_editor = Mock()
        self.schema_editor.connection.alias = "default"
        migration = import_module("travel.migrations.0010_travelreapprovalsnapshot")
        self.require_reapproval = migration.require_reapproval
        self.restore_approvals = migration.restore_approvals
        self.snapshot_model = apps.get_model("travel", "TravelReapprovalSnapshot")

    def make_event(self, name, **overrides):
        start = date.today() + timedelta(days=30)
        values = {
            "organization": self.organization,
            "program": self.program,
            "name": name,
            "start_date": start,
            "end_date": start + timedelta(days=2),
            "permission_form": "travel/permission_forms/permission.pdf",
        }
        values.update(overrides)
        return TravelEvent.objects.create(**values)

    def make_signup(self, event, **overrides):
        student = overrides.pop("student", None)
        if student is None:
            self.student_number += 1
            student = Student.objects.create(
                organization=self.organization,
                legal_first_name=f"Traveler{self.student_number}",
                last_name="Migration",
                school=self.school,
                graduation_year=2027,
            )
        values = {
            "organization": self.organization,
            "student": student,
            "event": event,
            "status": TravelSignup.APPROVED,
            "approved_by": self.parent,
            "approved_at": self.timestamp,
            "approved_total": Decimal("175.00"),
            "balance_amount": Decimal("240.00"),
            "balance_effective_date": date(2025, 6, 1),
            "permission_acknowledged": True,
            "permission_acknowledged_version": event.permission_form_version,
            "permission_acknowledged_by": "Test Parent",
            "permission_acknowledged_at": self.timestamp,
        }
        values.update(overrides)
        return TravelSignup.objects.create(**values)

    def test_reapproval_preserves_charges_and_acknowledgements_and_is_idempotent(self):
        past = date.today() - timedelta(days=30)
        past_event = self.make_event(
            "Past trip", start_date=past, end_date=past + timedelta(days=2)
        )
        upcoming_event = self.make_event("Upcoming trip")
        past_signup = self.make_signup(past_event)
        user = User.objects.create_user(
            username="migration-traveler", email="student@example.com"
        )
        student = Student.objects.create(
            organization=self.organization,
            user=user,
            legal_first_name="Upcoming",
            last_name="Traveler",
            school=self.school,
            graduation_year=2027,
        )
        AdultStudentRelationship.objects.create(
            organization=self.organization, adult=self.parent, student=student
        )
        upcoming_signup = self.make_signup(
            upcoming_event,
            student=student,
            balance_amount=Decimal("0.00"),
            balance_effective_date=None,
        )

        foreign_student = Student.objects.create(
            organization=self.other_organization,
            legal_first_name="Other",
            last_name="Traveler",
        )
        foreign_event = self.make_event(
            "Other organization trip",
            organization=self.other_organization,
            program=self.other_program,
        )
        foreign_signup = self.make_signup(
            foreign_event,
            organization=self.other_organization,
            student=foreign_student,
            approved_by=None,
            balance_amount=None,
        )
        eligible = [past_signup, upcoming_signup, foreign_signup]

        cost = TravelCostOption.objects.create(
            organization=self.organization,
            event=upcoming_event,
            name="Travel charge",
            amount=Decimal("240.00"),
        )
        upcoming_signup.cost_items.add(cost)
        signed_form = TravelSignedPermissionForm.objects.create(
            organization=self.organization,
            signup=upcoming_signup,
            form_version=upcoming_event.permission_form_version,
            file="travel/signed_permissions/existing.pdf",
        )
        signed_before = TravelSignedPermissionForm.objects.values().get(
            pk=signed_form.pk
        )

        no_form = self.make_signup(self.make_event("No form", permission_form=""))
        no_program = self.make_signup(self.make_event("No program", program=None))
        mismatched_event = self.make_signup(
            self.make_event("Foreign event", organization=self.other_organization)
        )
        mismatched_student = self.make_signup(
            self.make_event("Foreign student"), student=foreign_student
        )
        mismatched_program = self.make_signup(
            self.make_event("Foreign program", program=self.other_program)
        )
        mismatched_signup = self.make_signup(
            self.make_event("Foreign signup"), organization=self.other_organization
        )
        foreign_parent = Adult.objects.create(
            organization=self.other_organization,
            legal_first_name="Foreign",
            last_name="Parent",
            is_parent=True,
        )
        mismatched_parent = self.make_signup(
            self.make_event("Foreign approver"), approved_by=foreign_parent
        )
        departure_event = self.make_event("Foreign departure")
        mismatched_departure = self.make_signup(departure_event)
        foreign_departure = TravelDeparture.objects.create(
            organization=self.other_organization,
            event=departure_event,
            label="Foreign departure",
            leave_date=date(2035, 6, 1),
        )
        mismatched_departure.departure = foreign_departure
        mismatched_departure.save()
        cost_event = self.make_event("Foreign cost")
        mismatched_cost = self.make_signup(cost_event)
        foreign_cost = TravelCostOption.objects.create(
            organization=self.other_organization,
            event=cost_event,
            name="Foreign cost",
            amount=Decimal("20.00"),
        )
        mismatched_cost.cost_items.add(foreign_cost)
        pending = self.make_signup(upcoming_event, status=TravelSignup.INTERESTED)
        declined = self.make_signup(upcoming_event, status=TravelSignup.DECLINED)
        withdrawn = self.make_signup(upcoming_event, status=TravelSignup.WITHDRAWN)
        excluded = [
            no_form,
            no_program,
            mismatched_event,
            mismatched_student,
            mismatched_program,
            mismatched_signup,
            mismatched_parent,
            mismatched_departure,
            mismatched_cost,
            pending,
            declined,
            withdrawn,
        ]
        before = {
            signup.pk: TravelSignup.objects.values().get(pk=signup.pk)
            for signup in eligible + excluded
        }
        mail.get_connection()
        outbox_before = list(mail.outbox)
        snapshots_before = None

        for attempt in range(2):
            with self.subTest(attempt=attempt + 1):
                with self.captureOnCommitCallbacks(execute=True):
                    self.require_reapproval(apps, self.schema_editor)

                snapshots = list(
                    self.snapshot_model.objects.order_by("signup_id").values()
                )
                self.assertEqual(
                    {snapshot["signup_id"] for snapshot in snapshots},
                    {signup.pk for signup in eligible},
                )
                self.assertEqual(len(snapshots), len(eligible))
                for snapshot in snapshots:
                    with self.subTest(snapshot=snapshot["signup_id"]):
                        original = before[snapshot["signup_id"]]
                        for field in (
                            "organization_id",
                            "approved_by_id",
                            "approved_at",
                            "approved_total",
                        ):
                            self.assertEqual(snapshot[field], original[field])
                if snapshots_before is None:
                    snapshots_before = snapshots
                else:
                    self.assertEqual(snapshots, snapshots_before)

                for signup in eligible:
                    with self.subTest(signup=signup.pk):
                        expected = before[signup.pk].copy()
                        expected.update(
                            status=TravelSignup.INTERESTED,
                            approved_by_id=None,
                            approved_at=None,
                            approved_total=None,
                        )
                        self.assertEqual(
                            TravelSignup.objects.values().get(pk=signup.pk), expected
                        )
                for signup in excluded:
                    with self.subTest(signup=signup.pk):
                        self.assertEqual(
                            TravelSignup.objects.values().get(pk=signup.pk),
                            before[signup.pk],
                        )
                self.assertEqual(
                    TravelSignedPermissionForm.objects.values().get(pk=signed_form.pk),
                    signed_before,
                )
                self.assertEqual(list(upcoming_signup.cost_items.all()), [cost])
                self.assertEqual(mail.outbox, outbox_before)

    def test_reverse_rejects_snapshots_without_changing_signups(self):
        event = self.make_event("Trip with snapshots")
        untouched = self.make_signup(event)
        changed = self.make_signup(event)
        unrelated_pending = self.make_signup(
            event,
            status=TravelSignup.INTERESTED,
            approved_by=None,
            approved_at=None,
            approved_total=None,
        )
        signups = [untouched, changed, unrelated_pending]
        mail.get_connection()
        outbox_before = list(mail.outbox)

        with self.captureOnCommitCallbacks(execute=True):
            self.require_reapproval(apps, self.schema_editor)

        snapshots_before = list(
            self.snapshot_model.objects.order_by("signup_id").values()
        )
        self.assertEqual(
            {snapshot["signup_id"] for snapshot in snapshots_before},
            {untouched.pk, changed.pk},
        )
        before = {
            signup.pk: TravelSignup.objects.values().get(pk=signup.pk)
            for signup in signups
        }

        with self.assertRaises(RuntimeError):
            with self.captureOnCommitCallbacks(execute=True):
                self.restore_approvals(apps, self.schema_editor)

        for signup in signups:
            self.assertEqual(
                TravelSignup.objects.values().get(pk=signup.pk), before[signup.pk]
            )
        self.assertEqual(
            list(self.snapshot_model.objects.order_by("signup_id").values()),
            snapshots_before,
        )

        TravelSignup.objects.filter(pk=changed.pk).update(
            status=TravelSignup.APPROVED,
            approved_by=self.parent,
            approved_at=self.timestamp + timedelta(days=1),
            approved_total=Decimal("99.00"),
        )
        before = {
            signup.pk: TravelSignup.objects.values().get(pk=signup.pk)
            for signup in signups
        }

        with self.assertRaises(RuntimeError):
            with self.captureOnCommitCallbacks(execute=True):
                self.restore_approvals(apps, self.schema_editor)

        for signup in signups:
            self.assertEqual(
                TravelSignup.objects.values().get(pk=signup.pk), before[signup.pk]
            )
        self.assertEqual(
            list(self.snapshot_model.objects.order_by("signup_id").values()),
            snapshots_before,
        )
        self.assertEqual(mail.outbox, outbox_before)

    def test_reverse_succeeds_without_snapshots(self):
        no_form = self.make_signup(self.make_event("No form", permission_form=""))
        pending = self.make_signup(
            self.make_event("Pending trip"), status=TravelSignup.INTERESTED
        )
        signups = [no_form, pending]
        before = {
            signup.pk: TravelSignup.objects.values().get(pk=signup.pk)
            for signup in signups
        }
        mail.get_connection()
        outbox_before = list(mail.outbox)

        with self.captureOnCommitCallbacks(execute=True):
            self.require_reapproval(apps, self.schema_editor)
        self.assertFalse(self.snapshot_model.objects.exists())

        with self.captureOnCommitCallbacks(execute=True):
            self.restore_approvals(apps, self.schema_editor)

        self.assertFalse(self.snapshot_model.objects.exists())
        for signup in signups:
            self.assertEqual(
                TravelSignup.objects.values().get(pk=signup.pk), before[signup.pk]
            )
        self.assertEqual(mail.outbox, outbox_before)
