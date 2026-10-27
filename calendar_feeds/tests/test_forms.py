"""Tests for calendar_feeds forms."""

from datetime import timedelta

from django import forms
from django.test import TestCase
from django.utils import timezone

from calendar_feeds.forms import (
    CalendarFeedACLInlineFormSet,
    CalendarFeedForm,
)
from calendar_feeds.models import CalendarFeed, CalendarFeedACL
from calendar_feeds.tests.factories import make_feed
from programs.models import Program


class CalendarFeedFormProgramGroupingTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.active_prog = Program.objects.create(
            name="Active FTC Season",
            active=True,
            start_date=today - timedelta(days=30),
            end_date=today + timedelta(days=60),
        )
        self.upcoming_prog = Program.objects.create(
            name="Upcoming FRC Season",
            active=True,
            start_date=today + timedelta(days=90),
            end_date=today + timedelta(days=180),
        )
        self.past_prog = Program.objects.create(
            name="Past FLL Season",
            active=False,
            start_date=today - timedelta(days=300),
            end_date=today - timedelta(days=100),
        )

    def test_program_field_grouped_optgroups(self):
        form = CalendarFeedForm()
        rendered = str(form["program"])

        self.assertIn("Org-wide (no program)", rendered)
        self.assertIn("Active Programs", rendered)
        self.assertIn("Upcoming Programs", rendered)
        self.assertIn("Past / Archived Programs", rendered)
        self.assertIn("Active FTC Season", rendered)
        self.assertIn("Upcoming FRC Season", rendered)
        self.assertIn("Past FLL Season", rendered)

    def test_save_feed_with_program(self):
        data = {
            "name": "FTC Calendar",
            "slug": "ftc-cal",
            "color": "#0d6efd",
            "visibility": CalendarFeed.VISIBILITY_MEMBERS,
            "source_type": CalendarFeed.SOURCE_MANUAL,
            "program": self.active_prog.pk,
            "is_active": True,
        }
        form = CalendarFeedForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        feed = form.save()
        self.assertEqual(feed.program, self.active_prog)


class CalendarFeedACLInlineFormSetTests(TestCase):
    def test_unbound_new_feed_generates_all_four_role_forms(self):
        formset = CalendarFeedACLInlineFormSet(prefix="acls")
        self.assertFalse(formset.can_delete)
        self.assertEqual(len(formset.forms), 4)
        roles = [f.initial.get("role") for f in formset.forms]
        self.assertEqual(roles, ["Mentor", "Parent", "Student", "Alumni"])

        mentor_form = formset.forms[0]
        self.assertIsInstance(mentor_form.fields["role"].widget, forms.HiddenInput)
        self.assertEqual(mentor_form.initial["role"], "Mentor")
        self.assertTrue(mentor_form.initial["can_read"])
        self.assertTrue(mentor_form.initial["can_write"])

        parent_form = formset.forms[1]
        self.assertIsInstance(parent_form.fields["role"].widget, forms.HiddenInput)
        self.assertEqual(parent_form.initial["role"], "Parent")
        self.assertTrue(parent_form.initial["can_read"])
        self.assertFalse(parent_form.initial["can_write"])

        student_form = formset.forms[2]
        self.assertIsInstance(student_form.fields["role"].widget, forms.HiddenInput)
        self.assertEqual(student_form.initial["role"], "Student")
        self.assertTrue(student_form.initial["can_read"])
        self.assertFalse(student_form.initial["can_write"])

        alumni_form = formset.forms[3]
        self.assertIsInstance(alumni_form.fields["role"].widget, forms.HiddenInput)
        self.assertEqual(alumni_form.initial["role"], "Alumni")
        self.assertFalse(alumni_form.initial["can_read"])
        self.assertFalse(alumni_form.initial["can_write"])

    def test_unbound_existing_feed_with_no_acls_generates_all_four_role_forms(self):
        feed = make_feed()
        self.assertEqual(feed.acls.count(), 0)
        formset = CalendarFeedACLInlineFormSet(instance=feed, prefix="acls")
        self.assertEqual(len(formset.forms), 4)
        roles = [f.initial.get("role") for f in formset.forms]
        self.assertEqual(roles, ["Mentor", "Parent", "Student", "Alumni"])

    def test_unbound_existing_feed_with_partial_acls_generates_existing_and_missing_roles(
        self,
    ):
        feed = make_feed()
        CalendarFeedACL.objects.create(
            feed=feed, role="Mentor", can_read=True, can_write=True
        )
        self.assertEqual(feed.acls.count(), 1)

        formset = CalendarFeedACLInlineFormSet(instance=feed, prefix="acls")
        self.assertEqual(len(formset.forms), 4)

        # First form is the existing Mentor record
        self.assertEqual(formset.forms[0].instance.role, "Mentor")
        # Remaining 3 forms are the missing roles
        missing_roles = [f.initial.get("role") for f in formset.forms[1:]]
        self.assertEqual(missing_roles, ["Parent", "Student", "Alumni"])

    def test_unbound_existing_feed_with_all_acls_has_zero_extra_forms(self):
        feed = make_feed(
            acls={
                "Mentor": (True, True),
                "Parent": (True, False),
                "Student": (True, False),
                "Alumni": (False, False),
            }
        )
        self.assertEqual(feed.acls.count(), 4)
        formset = CalendarFeedACLInlineFormSet(instance=feed, prefix="acls")
        self.assertEqual(len(formset.forms), 4)
        self.assertEqual(formset.extra, 0)

    def test_bound_formset_saves_acls(self):
        feed = make_feed()
        data = {
            "acls-TOTAL_FORMS": "4",
            "acls-INITIAL_FORMS": "0",
            "acls-MIN_NUM_FORMS": "0",
            "acls-MAX_NUM_FORMS": "1000",
            "acls-0-role": "Mentor",
            "acls-0-can_read": "on",
            "acls-0-can_write": "on",
            "acls-1-role": "Parent",
            "acls-1-can_read": "on",
            "acls-2-role": "Student",
            "acls-2-can_read": "on",
            "acls-3-role": "Alumni",
        }
        formset = CalendarFeedACLInlineFormSet(data=data, instance=feed, prefix="acls")
        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()

        self.assertEqual(feed.acls.count(), 4)
        mentor_acl = feed.acls.get(role="Mentor")
        self.assertTrue(mentor_acl.can_read)
        self.assertTrue(mentor_acl.can_write)

        alumni_acl = feed.acls.get(role="Alumni")
        self.assertFalse(alumni_acl.can_read)
        self.assertFalse(alumni_acl.can_write)
