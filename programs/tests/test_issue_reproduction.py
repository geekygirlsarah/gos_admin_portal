from django.test import TestCase
from django.db.models import Value
from django.db.models.functions import Coalesce, Lower
from django.contrib.auth.models import User
from programs.models import Adult
from programs.forms import AdultForm
import django

class AndrewIdSponsorReproductionTest(TestCase):
    def setUp(self):
        # Create some adults
        # Mentors
        self.mentor1 = Adult.objects.create(
            first_name="Zebra",
            last_name="Alpha",
            is_mentor=True
        )
        self.mentor2 = Adult.objects.create(
            first_name="Alice",
            preferred_first_name="Betty",
            last_name="Gamma",
            is_mentor=True
        )
        self.mentor3 = Adult.objects.create(
            first_name="Charlie",
            last_name="Delta",
            is_mentor=True
        )
        
        # Non-mentors
        self.parent = Adult.objects.create(
            first_name="Parent",
            last_name="User",
            is_parent=True,
            is_mentor=False
        )
        self.alumni = Adult.objects.create(
            first_name="Alumni",
            last_name="User",
            is_alumni=True,
            is_mentor=False
        )

    def test_andrew_id_sponsor_queryset(self):
        form = AdultForm()
        queryset = form.fields['andrew_id_sponsor'].queryset
        
        # Requirement 2: Filter by mentors only
        mentor_ids = [self.mentor1.id, self.mentor2.id, self.mentor3.id]
        queryset_ids = list(queryset.values_list('id', flat=True))
        
        for m_id in mentor_ids:
            self.assertIn(m_id, queryset_ids, f"Mentor {m_id} should be in queryset")
        
        self.assertNotIn(self.parent.id, queryset_ids, "Parent should not be in queryset")
        self.assertNotIn(self.alumni.id, queryset_ids, "Alumni should not be in queryset")
        
        # Requirement 1: Sorting
        # Expected order:
        # 1. Betty Gamma (Alice) - preferred "Betty"
        # 2. Charlie Delta
        # 3. Zebra Alpha
        
        expected_order = [self.mentor2.id, self.mentor3.id, self.mentor1.id]
        actual_order = list(queryset.values_list('id', flat=True))
        
        self.assertEqual(actual_order, expected_order, f"Expected order {expected_order}, got {actual_order}")
