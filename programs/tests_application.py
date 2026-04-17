from django.test import TestCase, Client
from django.urls import reverse
from programs.models import Program, Application, DisclosureForm, ApplicationFieldConfig
from django.core import mail

class ApplicationWizardTest(TestCase):
    def setUp(self):
        from django.utils import timezone
        from datetime import timedelta
        self.program = Program.objects.create(
            name="Test Program", 
            active=True,
            start_date=timezone.now().date() + timedelta(days=1),
            end_date=timezone.now().date() + timedelta(days=30)
        )
        self.client = Client()

    def test_full_student_application_flow(self):
        # 1. Start: Role Selection
        response = self.client.get(reverse('apply_start'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Step 1: Get Started")

        response = self.client.post(reverse('apply_start'), {
            'role_type': 'student_parent',
            'program': self.program.pk
        })
        self.assertEqual(response.status_code, 302)
        
        # Check if application created
        app = Application.objects.get()
        self.assertEqual(app.role_type, 'student_parent')
        self.assertEqual(app.current_step, 'identity')

        # 2. Identity
        response = self.client.get(reverse('apply_start'))
        self.assertContains(response, "Step 2: Your Identity")
        
        response = self.client.post(reverse('apply_start'), {
            'email': 'student@example.com'
        })
        self.assertEqual(response.status_code, 302)
        app.refresh_from_db()
        self.assertEqual(app.email_to_verify, 'student@example.com')
        self.assertIsNotNone(app.otp_code)
        self.assertEqual(len(mail.outbox), 1)

        # 3. OTP Verification
        response = self.client.post(reverse('apply_start'), {
            'otp': app.otp_code
        })
        self.assertEqual(response.status_code, 302)
        app.refresh_from_db()
        self.assertTrue(app.is_verified)
        self.assertEqual(app.current_step, 'data_entry')

        # 4. Data Entry (Student Info)
        response = self.client.get(reverse('apply_start'))
        self.assertContains(response, "Step 4: Student Information")
        
        # Grade 9 (older student)
        response = self.client.post(reverse('apply_start'), {
            'legal_first_name': 'Jane',
            'last_name': 'Doe',
            'grade': '9',
            'parent_email': 'parent@example.com'
        })
        # Should redirect to handoff page for older student
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Application Paused")
        app.refresh_from_db()
        self.assertEqual(app.status, 'pending_handoff')
        self.assertEqual(len(mail.outbox), 2) # Second email is handoff

        # 5. Parent resumes
        # Clear session to simulate parent starting fresh with code
        self.client.session.flush()
        response = self.client.get(reverse('apply_start'), {'code': app.application_code})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Step 4: Parent Verification")

        app.refresh_from_db()
        parent_otp = app.parent_otp_code

        # Verify Parent OTP
        response = self.client.post(reverse('apply_start'), {
            'otp': parent_otp
        })
        self.assertEqual(response.status_code, 302)
        app.refresh_from_db()
        self.assertTrue(app.is_parent_verified)

        response = self.client.get(reverse('apply_start'))
        # print(response.content.decode())
        self.assertIn(response.status_code, [200, 302])

        response = self.client.post(reverse('apply_start'), {
            'p1_first_name': 'John',
            'p1_last_name': 'Doe',
            'p1_email': 'parent@example.com',
            'p1_phone': '555-1234',
            'p1_relationship': 'father'
        })
        # print(response.content.decode())
        self.assertIn(response.status_code, [200, 302])
        app.refresh_from_db()
        self.assertEqual(app.status, 'pending_approval')

    def test_mentor_application_flow(self):
        # 1. Start: Role Selection
        self.client.post(reverse('apply_start'), {
            'role_type': 'mentor_volunteer',
            'program': self.program.pk
        })
        app = Application.objects.get()
        self.assertEqual(app.role_type, 'mentor_volunteer')

        # 2. Identity & OTP (skip detailed checks, verified in student flow)
        self.client.post(reverse('apply_start'), {'email': 'mentor@example.com'})
        app.refresh_from_db()
        self.client.post(reverse('apply_start'), {'otp': app.otp_code})
        app.refresh_from_db()
        self.assertTrue(app.is_verified)

        # 3. Mentor Data Entry
        response = self.client.get(reverse('apply_start'))
        self.assertContains(response, "Step 4: Mentor/Volunteer Information")

        response = self.client.post(reverse('apply_start'), {
            'legal_first_name': 'Mike',
            'last_name': 'Mentor',
            'cell_phone_number': '555-9999'
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('apply_thanks'))
        app.refresh_from_db()
        self.assertEqual(app.status, 'pending_approval')
