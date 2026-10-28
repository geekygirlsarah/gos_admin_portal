from django.test import TestCase

from programs.models import Adult


class AdultModelTests(TestCase):
    def test_adult_email_shared_allowed(self):
        # Two adults (e.g. a mother and father) may share the same personal
        # email address — the unique constraint was intentionally removed to
        # support this real-world case.
        Adult.objects.create(
            legal_first_name="Mary",
            last_name="Smith",
            personal_email="shared@example.com",
        )
        # Should NOT raise — different person, same email
        Adult.objects.create(
            legal_first_name="John",
            last_name="Smith",
            personal_email="shared@example.com",
        )
        self.assertEqual(
            Adult.objects.filter(personal_email="shared@example.com").count(), 2
        )

    def test_adult_email_null_allowed_multiple_times(self):
        # Multiple adults with NULL email should be allowed
        Adult.objects.create(legal_first_name="A", last_name="B", personal_email=None)
        Adult.objects.create(legal_first_name="C", last_name="D", personal_email=None)
        self.assertEqual(Adult.objects.filter(personal_email__isnull=True).count(), 2)

    def test_adult_display_name_and_full_name(self):
        a1 = Adult(legal_first_name="Robert", last_name="Johnson")
        self.assertEqual(a1.display_name, "Robert Johnson")
        self.assertEqual(a1.full_name, "Robert Johnson")

        a2 = Adult(
            legal_first_name="Robert",
            preferred_first_name="Bob",
            last_name="Johnson",
        )
        self.assertEqual(a2.display_name, "Bob Johnson")
        self.assertEqual(a2.full_name, "Bob Johnson")
