"""Tests for the outreach saved-locations feature.

Covers the ``OutreachLocation`` reference model, the location picker wired
into the event form, and the mentor/Lead Mentor location management CRUD
(list/create/edit/delete, including the AJAX create used by the event form).
"""

from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from outreach.forms import OutreachEventForm
from outreach.models import OutreachEvent, OutreachLocation, OutreachShift
from outreach.tests.factories import create_outreach_event
from programs.models import Adult, Program, ProgramFeature, School, Student


def _create_outreach_program():
    program = Program.objects.create(name="Outreach Test Program", active=True)
    feature, _ = ProgramFeature.objects.get_or_create(key="outreach")
    program.features.add(feature)
    return program


class OutreachLocationModelTest(TestCase):
    def test_str_uses_name(self):
        location = OutreachLocation.objects.create(
            name="Carnegie Science Center", address="1 Allegheny Ave, Pittsburgh, PA"
        )
        self.assertEqual(str(location), "Carnegie Science Center")

    def test_duplicate_name_and_address_rejected(self):
        OutreachLocation.objects.create(name="Library", address="100 Main St")
        with self.assertRaises(IntegrityError), transaction.atomic():
            OutreachLocation.objects.create(name="Library", address="100 Main St")

    def test_same_name_different_address_allowed(self):
        OutreachLocation.objects.create(name="Library", address="100 Main St")
        other = OutreachLocation(name="Library", address="200 Oak Ave")
        other.full_clean(exclude=["created_at", "updated_at"])  # no raise
        other.save()


class OutreachLocationViewTest(TestCase):
    def setUp(self):
        self.lead_mentor_group, _ = Group.objects.get_or_create(name="LeadMentor")

        self.school = School.objects.create(name="Test High")

        self.mentor_user = User.objects.create_user(
            username="outreach_mentor", password="password"  # nosec B106
        )
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user, is_mentor=True, mentor_active=True
        )

        self.lead_user = User.objects.create_user(
            username="outreach_lead", password="password"  # nosec B106
        )
        self.lead_user.groups.add(self.lead_mentor_group)
        self.lead_adult = Adult.objects.create(
            user=self.lead_user, is_mentor=True, mentor_active=True
        )

        self.student_user = User.objects.create_user(
            username="outreach_student", password="password"  # nosec B106
        )
        self.student = Student.objects.create(
            user=self.student_user,
            legal_first_name="Maya",
            last_name="Lin",
            school=self.school,
            graduation_year=2028,
        )

        self.parent_user = User.objects.create_user(
            username="outreach_parent", password="password"  # nosec B106
        )
        self.parent_adult = Adult.objects.create(user=self.parent_user, is_parent=True)

        self.program = _create_outreach_program()

        self.location = OutreachLocation.objects.create(
            name="Carnegie Science Center", address="1 Allegheny Ave"
        )
        self.dog_park = OutreachLocation.objects.create(
            name="Dog Park", address="500 Off Leash Rd"
        )

        self.list_url = reverse("outreach:location_list", args=[self.program.id])
        self.create_url = reverse("outreach:location_create", args=[self.program.id])

    def test_list_view_allowed_for_mentor_and_lead(self):
        self.client.force_login(self.mentor_user)
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Carnegie Science Center")

        self.client.force_login(self.lead_user)
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Carnegie Science Center")

    def test_list_view_denied_for_student_and_parent(self):
        self.client.force_login(self.student_user)
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, 302)

        self.client.force_login(self.parent_user)
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, 302)

    def test_list_view_denied_when_program_lacks_outreach(self):
        plain_program = Program.objects.create(name="No Outreach Program")
        url = reverse("outreach:location_list", args=[plain_program.id])
        self.client.force_login(self.mentor_user)
        # OutreachProgramMixin raises Http404 for programs without the
        # outreach feature; the project's handler404 redirects to home.
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(resp.url, ("/", "/profile/"))

    def test_create_page_and_post(self):
        self.client.force_login(self.mentor_user)
        resp = self.client.get(self.create_url)
        self.assertEqual(resp.status_code, 200)

        resp = self.client.post(
            self.create_url,
            {"name": "North Hills Community Center", "address": "123 Main St"},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            OutreachLocation.objects.filter(
                name="North Hills Community Center", address="123 Main St"
            ).exists()
        )

    def test_ajax_create_returns_json(self):
        self.client.force_login(self.mentor_user)
        resp = self.client.post(
            self.create_url,
            {"name": "City Library", "address": "10 Reading Way"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["name"], "City Library")
        self.assertEqual(data["address"], "10 Reading Way")

    def test_ajax_create_invalid_returns_errors(self):
        self.client.force_login(self.mentor_user)
        resp = self.client.post(
            self.create_url,
            {"name": "", "address": ""},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data["ok"])
        self.assertIn("name", data["errors"])

    def test_edit_view_updates_location(self):
        self.client.force_login(self.lead_user)
        url = reverse(
            "outreach:location_edit", args=[self.program.id, self.location.id]
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        resp = self.client.post(
            url,
            {
                "name": "Carnegie Science Center (North Shore)",
                "address": "1 Allegheny Ave",
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.location.refresh_from_db()
        self.assertEqual(self.location.name, "Carnegie Science Center (North Shore)")

    def test_delete_view_removes_location(self):
        self.client.force_login(self.lead_user)
        url = reverse(
            "outreach:location_delete", args=[self.program.id, self.location.id]
        )
        resp = self.client.post(url, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(OutreachLocation.objects.filter(pk=self.location.id).exists())

    def test_delete_confirm_page_renders(self):
        self.client.force_login(self.lead_user)
        url = reverse(
            "outreach:location_delete", args=[self.program.id, self.location.id]
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)


class OutreachEventFormLocationPickerTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test High")
        self.program = _create_outreach_program()
        self.location = OutreachLocation.objects.create(
            name="Carnegie Science Center", address="1 Allegheny Ave, Pittsburgh, PA"
        )
        self.event = create_outreach_event(
            program=self.program,
            name="STEM Festival",
            location_name="Carnegie Science Center",
            location_address="1 Allegheny Ave, Pittsburgh, PA",
            start_date=timezone.now().date(),
            start_time="10:00:00",
            end_time="12:00:00",
        )

    def test_form_exposes_saved_location_field(self):
        form = OutreachEventForm()
        self.assertIn("saved_location", form.fields)
        self.assertFalse(form.fields["saved_location"].required)
        choices = form.fields["saved_location"].queryset
        self.assertEqual(list(choices), [self.location])

    def test_form_preselects_matching_saved_location_on_edit(self):
        form = OutreachEventForm(instance=self.event)
        self.assertEqual(form.fields["saved_location"].initial, self.location.pk)

    def test_form_does_not_preselect_non_matching_location_on_edit(self):
        self.event.location_address = "123 Other St"
        form = OutreachEventForm(instance=self.event)
        self.assertIsNone(form.fields["saved_location"].initial)

    def test_submitting_saved_location_still_saves_typed_text(self):
        form = OutreachEventForm(
            data={
                "saved_location": str(self.location.pk),
                "name": "STEM Festival",
                "location_name": "Carnegie Science Center",
                "location_address": "1 Allegheny Ave, Pittsburgh, PA",
                "description": "",
            },
            instance=self.event,
        )
        self.assertTrue(form.is_valid(), form.errors)
        event = form.save()
        self.assertEqual(event.location_name, "Carnegie Science Center")
        self.assertEqual(event.location_address, "1 Allegheny Ave, Pittsburgh, PA")


class OutreachEventCreateContextTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test High")
        self.program = _create_outreach_program()

        self.mentor_user = User.objects.create_user(
            username="outreach_mentor_ctx", password="password"  # nosec B106
        )
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user, is_mentor=True, mentor_active=True
        )

        self.student_user = User.objects.create_user(
            username="outreach_student_ctx", password="password"  # nosec B106
        )
        self.student = Student.objects.create(
            user=self.student_user,
            legal_first_name="Maya",
            last_name="Lin",
            school=self.school,
            graduation_year=2028,
        )

        OutreachLocation.objects.create(
            name="Carnegie Science Center", address="1 Allegheny Ave, Pittsburgh, PA"
        )
        self.url = reverse("outreach:event_create", args=[self.program.id])

    def test_mentor_create_context_has_picker_data(self):
        self.client.force_login(self.mentor_user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        context = resp.context
        self.assertTrue(context["can_add_location"])
        self.assertEqual(len(context["locations_data"]), 1)
        self.assertContains(resp, "id_saved_location")

    def test_student_create_context_has_no_add_permission(self):
        self.client.force_login(self.student_user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        context = resp.context
        self.assertFalse(context["can_add_location"])
        self.assertEqual(len(context["locations_data"]), 1)
        self.assertContains(resp, "id_saved_location")
        self.assertNotContains(resp, 'id="addLocationModal"')

    def test_event_create_with_saved_location_picker(self):
        self.client.force_login(self.mentor_user)
        location = OutreachLocation.objects.get(name="Carnegie Science Center")
        data = {
            "saved_location": str(location.pk),
            "name": "New Pickation Event",
            "location_name": location.name,
            "location_address": location.address,
            "description": "",
            "shifts-TOTAL_FORMS": "1",
            "shifts-INITIAL_FORMS": "0",
            "shifts-MIN_NUM_FORMS": "1",
            "shifts-MAX_NUM_FORMS": "1000",
            "shifts-0-date": (timezone.now().date() + timedelta(days=3)).isoformat(),
            "shifts-0-start_time": "10:00",
            "shifts-0-end_time": "12:00",
            "shifts-0-max_champions": "1",
            "shifts-0-max_helpers": "5",
        }
        resp = self.client.post(self.url, data, follow=True)
        self.assertEqual(resp.status_code, 200)
        event = OutreachEvent.objects.get(name="New Pickation Event")
        self.assertEqual(event.location_name, "Carnegie Science Center")
        self.assertEqual(OutreachShift.objects.filter(event=event).count(), 1)
