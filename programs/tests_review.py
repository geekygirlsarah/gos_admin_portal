from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User, Group
from programs.models import Program, Application, DisclosureForm
import datetime

class ApplicationReviewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.lead_mentor_user = User.objects.create_superuser(username='leadmentor', email='lead@example.com', password='password')
        
        # Create a group LeadMentor just in case mixin checks for it (it checks both superuser OR group)
        self.group, _ = Group.objects.get_or_create(name='LeadMentor')
        self.lead_mentor_user.groups.add(self.group)
        
        self.program = Program.objects.create(
            name="Test Program",
            start_date=datetime.date.today() + datetime.timedelta(days=10),
            end_date=datetime.date.today() + datetime.timedelta(days=20),
        )
        
        # Create a pending application
        self.app = Application.objects.create(
            application_code="ABC123XY",
            email_to_verify="applicant@example.com",
            role_type="mentor_volunteer",
            program=self.program,
            status="pending_approval",
            data={"mentor_info": {"first_name": "John", "last_name": "Doe", "email": "applicant@example.com"}}
        )

    def test_review_list_access(self):
        # Unauthenticated access
        response = self.client.get(reverse('application_review_list'))
        self.assertEqual(response.status_code, 302) # Redirect to login
        
        # Authenticated access
        self.client.login(username='leadmentor', password='password')
        response = self.client.get(reverse('application_review_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ABC123XY")
        self.assertContains(response, "John")
        self.assertContains(response, "Doe")

    def test_review_detail_access(self):
        self.client.login(username='leadmentor', password='password')
        response = self.client.get(reverse('application_review_detail', args=[self.app.pk]))
        self.assertEqual(response.status_code, 200)
        # print(response.content.decode())
        self.assertContains(response, "John")
        self.assertContains(response, "Doe")
        self.assertContains(response, "Approve Application")

    def test_approve_application(self):
        self.client.login(username='leadmentor', password='password')
        response = self.client.post(reverse('application_review_detail', args=[self.app.pk]), {'action': 'approve'})
        self.assertEqual(response.status_code, 302)
        
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, "pending_disclosures")

    def test_deny_application(self):
        self.client.login(username='leadmentor', password='password')
        response = self.client.post(reverse('application_review_detail', args=[self.app.pk]), {'action': 'deny'})
        self.assertEqual(response.status_code, 302)
        
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, "rejected")

    def test_disclosure_upload_view_access(self):
        # Create a disclosure form so the view has something to show
        from django.core.files.base import ContentFile
        pdf_content = ContentFile(b"fake pdf content", name="test.pdf")
        DisclosureForm.objects.create(name="Waiver", required_for_role="all", is_active=True, pdf_file=pdf_content)
        
        # App is in pending_approval, shouldn't access disclosures yet
        response = self.client.get(reverse('disclosure_upload', args=[self.app.application_code]))
        self.assertEqual(response.status_code, 302)
        
        # Approve it
        self.app.status = "pending_disclosures"
        self.app.save()
        
        # Re-fetch or check logic
        response = self.client.get(reverse('disclosure_upload', args=[self.app.application_code]))
        # It might redirect to login if it's protected? 
        # Actually DisclosureUploadView is NOT protected by LoginRequiredMixin because applicants aren't users yet.
        if response.status_code == 302:
            print(f"Redirected to: {response.url}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Waiver") 
