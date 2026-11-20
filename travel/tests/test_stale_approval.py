from datetime import timedelta
from decimal import Decimal
from html.parser import HTMLParser

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from organizations.models import Organization
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Program,
    ProgramFeature,
    School,
    Student,
)
from travel.models import TravelCostOption, TravelDeparture, TravelEvent, TravelSignup


class ApprovalFormParser(HTMLParser):
    def __init__(self, action):
        super().__init__()
        self.action = action
        self.in_form = False
        self.tokens = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.in_form = attrs.get("action") == self.action
        elif tag == "input" and self.in_form and attrs.get("name") == "approval_token":
            self.tokens.append(attrs.get("value"))

    def handle_endtag(self, tag):
        if tag == "form":
            self.in_form = False


class StaleTravelApprovalTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.get(slug="gos")
        self.program = Program.objects.create(
            organization=self.organization, name="Travel Approval Program"
        )
        feature, _ = ProgramFeature.objects.get_or_create(
            key="travel", defaults={"name": "Travel"}
        )
        self.program.features.add(feature)
        school = School.objects.create(name="Travel Approval School")
        self.student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Test",
            last_name="Traveler",
            school=school,
            graduation_year=2027,
        )
        Enrollment.objects.create(
            organization=self.organization,
            student=self.student,
            program=self.program,
            active=True,
        )
        self.guardian = User.objects.create_user(username="approval-guardian")
        self.guardian_adult = Adult.objects.create(
            organization=self.organization,
            user=self.guardian,
            is_parent=True,
            login_enabled=True,
        )
        AdultStudentRelationship.objects.create(
            organization=self.organization,
            adult=self.guardian_adult,
            student=self.student,
        )
        start = timezone.localdate() + timedelta(days=30)
        self.event = TravelEvent.objects.create(
            organization=self.organization,
            program=self.program,
            name="Approval Trip",
            start_date=start,
            end_date=start + timedelta(days=2),
            permission_form="travel/forms/blank.pdf",
        )
        self.departure = TravelDeparture.objects.create(
            organization=self.organization,
            event=self.event,
            label="First bus",
            leave_date=start,
        )
        self.other_departure = TravelDeparture.objects.create(
            organization=self.organization,
            event=self.event,
            label="Second bus",
            leave_date=start + timedelta(days=1),
        )
        self.cost = TravelCostOption.objects.create(
            organization=self.organization,
            event=self.event,
            name="Hotel",
            amount=Decimal("50.00"),
            is_required=False,
        )
        self.signup = TravelSignup.objects.create(
            organization=self.organization,
            event=self.event,
            student=self.student,
            departure=self.departure,
        )
        self.signup.cost_items.add(self.cost)
        self.list_url = reverse("travel:event_list", args=[self.program.pk])
        self.approve_url = reverse(
            "travel:signup_approve", args=[self.program.pk, self.signup.pk]
        )
        self.client.force_login(self.guardian)

    def load_token(self, *expected_details):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        for detail in expected_details:
            self.assertContains(response, detail)
        parser = ApprovalFormParser(self.approve_url)
        parser.feed(response.content.decode())
        self.assertEqual(len(parser.tokens), 1)
        self.assertTrue(parser.tokens[0])
        return parser.tokens[0]

    def post_approval(self, token=None):
        payload = {"permission_acknowledged": "1"}
        if token is not None:
            payload["approval_token"] = token
        return self.client.post(self.approve_url, payload)

    def assert_pending_unchanged(self, balance=None):
        self.signup.refresh_from_db()
        self.assertEqual(self.signup.status, TravelSignup.INTERESTED)
        self.assertIsNone(self.signup.approved_total)
        self.assertIsNone(self.signup.approved_by)
        self.assertIsNone(self.signup.approved_at)
        self.assertFalse(self.signup.permission_acknowledged)
        self.assertIsNone(self.signup.permission_acknowledged_version)
        self.assertEqual(self.signup.permission_acknowledged_by, "")
        self.assertIsNone(self.signup.permission_acknowledged_at)
        self.assertEqual(self.signup.balance_amount, balance)

    def assert_approved(self, total):
        self.signup.refresh_from_db()
        self.assertEqual(self.signup.status, TravelSignup.APPROVED)
        self.assertEqual(self.signup.approved_by, self.guardian_adult)
        self.assertIsNotNone(self.signup.approved_at)
        self.assertEqual(self.signup.approved_total, Decimal(total))
        self.assertEqual(self.signup.balance_amount, Decimal(total))
        self.assertTrue(self.signup.permission_acknowledged)
        self.event.refresh_from_db()
        self.assertEqual(
            self.signup.permission_acknowledged_version,
            self.event.permission_form_version,
        )

    def assert_stale_then_reloaded(self, token, total, *expected_details):
        response = self.post_approval(token)
        self.assertEqual(response.status_code, 302)
        self.assert_pending_unchanged()
        fresh_token = self.load_token(*expected_details)
        self.assertNotEqual(token, fresh_token)
        response = self.post_approval(fresh_token)
        self.assertEqual(response.status_code, 302)
        self.assert_approved(total)

    def test_guardian_approves_current_rendered_snapshot(self):
        token = self.load_token("First bus", "Hotel", "$50.00")
        response = self.post_approval(token)
        self.assertEqual(response.status_code, 302)
        self.assert_approved("50.00")

    def test_missing_and_forged_tokens_are_denied(self):
        token = self.load_token()
        for invalid_token in (None, "", token + "forged"):
            with self.subTest(token=invalid_token):
                response = self.post_approval(invalid_token)
                self.assertEqual(response.status_code, 302)
                self.assert_pending_unchanged()

    def test_unlinked_guardian_cannot_approve_with_valid_token(self):
        token = self.load_token()
        other_user = User.objects.create_user(username="unlinked-approval-guardian")
        Adult.objects.create(
            organization=self.organization,
            user=other_user,
            is_parent=True,
            login_enabled=True,
        )
        self.client.force_login(other_user)
        response = self.post_approval(token)
        self.assertEqual(response.status_code, 302)
        self.assert_pending_unchanged()

    def test_departure_change_invalidates_loaded_token(self):
        token = self.load_token()
        self.signup.departure = self.other_departure
        self.signup.save(update_fields=["departure"])
        self.assert_stale_then_reloaded(token, "50.00", "Second bus", "$50.00")

    def test_selected_cost_change_invalidates_loaded_token(self):
        token = self.load_token()
        self.signup.cost_items.clear()
        self.assert_stale_then_reloaded(token, "0.00", "Current trip cost: $0.00")

    def test_selected_cost_price_change_invalidates_loaded_token(self):
        token = self.load_token()
        self.cost.amount = Decimal("75.00")
        self.cost.save(update_fields=["amount"])
        self.assert_stale_then_reloaded(token, "75.00", "Hotel", "$75.00")

    def test_blank_permission_form_replacement_invalidates_loaded_token(self):
        token = self.load_token()
        self.event.permission_form = "travel/forms/replacement.pdf"
        self.event.save(update_fields=["permission_form"])
        self.assert_stale_then_reloaded(token, "50.00", "replacement.pdf")

    def test_past_trip_form_supplies_a_usable_token(self):
        self.event.start_date = timezone.localdate() - timedelta(days=10)
        self.event.end_date = timezone.localdate() - timedelta(days=8)
        self.event.save(update_fields=["start_date", "end_date"])
        token = self.load_token("Past Trips", "First bus", "Hotel", "$50.00")
        response = self.post_approval(token)
        self.assertEqual(response.status_code, 302)
        self.assert_approved("50.00")
