from datetime import date, timedelta
from threading import Event, Thread
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.db import close_old_connections, transaction
from django.test import RequestFactory, TransactionTestCase, skipUnlessDBFeature

from organizations.models import Organization, UserOrganizationMembership
from programs.models import Program, ProgramFeature, Student
from travel.models import TravelEvent, TravelSignup
from travel.views import TravelSignupWithdrawView


class TravelConcurrentTransitionTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.organization = Organization.objects.create(
            name="Travel Lock Test",
            slug="travel-lock-test",
            subdomain="travel-lock-test",
        )
        feature, _ = ProgramFeature.objects.get_or_create(
            key="travel", defaults={"name": "Travel"}
        )
        self.program = Program.objects.create(
            organization=self.organization,
            name="Travel Lock Program",
        )
        self.program.features.add(feature)
        self.event = TravelEvent.objects.create(
            organization=self.organization,
            program=self.program,
            name="Travel Lock Event",
            start_date=date.today() + timedelta(days=10),
            end_date=date.today() + timedelta(days=11),
        )
        self.student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Lock",
            last_name="Test",
        )
        self.signup = TravelSignup.objects.create(
            organization=self.organization,
            student=self.student,
            event=self.event,
            status=TravelSignup.INTERESTED,
        )
        self.lead = User.objects.create_user(
            username="travel-lock-lead",
            email="travel-lock-lead@example.com",
            password="password",
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=self.lead,
            organization=self.organization,
            role="LeadMentor",
            is_active=True,
        )

    @skipUnlessDBFeature("has_select_for_update")
    def test_withdraw_waits_for_event_lock_and_rechecks_signup_state(self):
        action_reached = Event()
        continue_action = Event()
        worker_finished = Event()
        responses = []
        errors = []
        original_may_act = TravelSignupWithdrawView._may_act
        factory = RequestFactory()

        def pause_action(view, user, signup, org=None):
            action_reached.set()
            if not continue_action.wait(timeout=5):
                raise TimeoutError("Transition test did not release its worker")
            return original_may_act(view, user, signup, org=org)

        def withdraw_in_worker():
            close_old_connections()
            try:
                request = factory.post("/")
                request.user = self.lead
                request.organization = self.organization
                request._messages = Mock()
                responses.append(
                    TravelSignupWithdrawView.as_view()(
                        request,
                        program_id=self.program.pk,
                        pk=self.signup.pk,
                    )
                )
            except Exception as exc:
                errors.append(exc)
            finally:
                worker_finished.set()
                close_old_connections()

        worker = Thread(target=withdraw_in_worker)
        with (
            patch.object(TravelSignupWithdrawView, "_may_act", new=pause_action),
            patch("travel.views.notify_signup_withdrawn") as notify,
        ):
            try:
                with transaction.atomic():
                    TravelEvent.objects.select_for_update().get(pk=self.event.pk)
                    worker.start()
                    self.assertFalse(
                        action_reached.wait(timeout=0.5),
                        "The action should wait for the trip lock before reading state",
                    )
                    self.signup.status = TravelSignup.DECLINED
                    self.signup.save(update_fields=["status"])
            finally:
                continue_action.set()
                if worker.ident is not None:
                    worker.join(timeout=5)

        self.assertTrue(worker_finished.is_set())
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0].status_code, 302)
        self.signup.refresh_from_db()
        self.assertEqual(self.signup.status, TravelSignup.DECLINED)
        notify.assert_not_called()
