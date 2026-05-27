from __future__ import annotations

import datetime

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from programs.models import Adult, Program, Student


def _verified(**kwargs):
    """Convenience: create an application that has cleared Steps 1-4."""
    defaults = dict(
        applicant_type=Application.Type.PARENT,
        email="parent@example.com",
        current_step=8,  # Start at step 8
        email_verified_at=timezone.now(),
        status=Application.Status.EMAIL_VERIFIED,
    )
    defaults.update(kwargs)
    return Application.objects.create(**defaults)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class TestStep8Repopulation(TestCase):
    def setUp(self):
        self.program = Program.objects.create(
            name="Spring 2030",
            year=2030,
            start_date=timezone.localdate() + datetime.timedelta(days=60),
            end_date=timezone.localdate() + datetime.timedelta(days=120),
            active=True,
        )

    def test_step8_repopulates_when_navigating_back(self):
        # Need some application data to pass handoff check
        app = _verified(program=self.program)
        app.data = {"step5": {"address": "123 Main St"}}
        app.save()

        # 1. Post valid info to step 8
        self.client.post(
            reverse("apply_step8", kwargs={"app_id": app.application_id}),
            {
                "first_name": "Secondary",
                "last_name": "Parent",
                "relationship_to_student": "guardian",
                "email": "secondary@example.com",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15213",
                "cell_phone": "123-456-7890",
            },
        )

        # Verify it advanced to step 9
        app.refresh_from_db()
        self.assertEqual(app.current_step, 9)
        self.assertIn("step8", app.data)

        # 2. Go back to step 8
        response = self.client.get(
            reverse("apply_step8", kwargs={"app_id": app.application_id})
        )

        # 3. Check if data is populated in the form
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="Secondary"')
