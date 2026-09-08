from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse

from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Program,
    School,
    Student,
)


@override_settings(FILE_ENCRYPTION_KEY="ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg=")
class StudentAdultDirectoryUITests(TestCase):
    def setUp(self):
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")

        self.lead_user = User.objects.create_user(
            username="leadmentor@example.com",
            email="leadmentor@example.com",
            password="password",  # nosec B106
        )
        self.lead_user.groups.add(self.lead_group)

        self.lead_adult = Adult.objects.create(
            user=self.lead_user,
            legal_first_name="Lead",
            last_name="Mentor",
            is_mentor=True,
            mentor_active=True,
        )

        self.program = Program.objects.create(
            name="Girls of Steel FRC",
            active=True,
        )

        self.school = School.objects.create(name="Carnegie Mellon High")

        self.student1 = Student.objects.create(
            legal_first_name="Jane",
            preferred_first_name="Janey",
            last_name="Doe",
            personal_email="janey@example.com",
            phone_number="412-555-0101",
            school=self.school,
            graduation_year=2027,
            graduated=False,
            allergies="Peanuts",
        )

        self.student2 = Student.objects.create(
            legal_first_name="Alex",
            last_name="Smith",
            personal_email="alex@example.com",
            graduation_year=2025,
            graduated=True,
        )

        Enrollment.objects.create(
            program=self.program,
            student=self.student1,
            active=True,
        )

        self.parent1 = Adult.objects.create(
            legal_first_name="Mary",
            last_name="Doe",
            personal_email="mary.doe@example.com",
            phone_number="412-555-0199",
            is_parent=True,
            email_updates=True,
        )
        AdultStudentRelationship.objects.create(
            adult=self.parent1,
            student=self.student1,
            relationship_to_student="mother",
        )
        self.student1.primary_contact = self.parent1
        self.student1.save()

        self.alumni1 = Adult.objects.create(
            legal_first_name="Sarah",
            last_name="Alum",
            personal_email="sarah.alum@example.com",
            is_alumni=True,
            ok_to_contact=True,
            college="MIT",
            employer="Robotics Corp",
        )

    def test_student_list_kpi_and_navigation(self):
        self.client.force_login(self.lead_user)
        resp = self.client.get(reverse("student_list"))
        self.assertEqual(resp.status_code, 200)

        # Context metrics
        self.assertEqual(resp.context["total_students_count"], 2)
        self.assertEqual(resp.context["active_students_count"], 1)
        self.assertEqual(resp.context["graduated_count"], 1)
        self.assertEqual(resp.context["unique_schools_count"], 1)

        content = resp.content.decode()
        self.assertIn("Total Students", content)
        self.assertIn("Active Students", content)
        self.assertIn("Alumni / Graduated", content)
        self.assertIn("Schools", content)
        self.assertIn("Photo Grid", content)
        self.assertIn("By Grade", content)
        self.assertIn("By School", content)
        self.assertIn("Emergency Contacts", content)
        self.assertIn("Janey Doe", content)

    def test_student_detail_hero_and_sections(self):
        self.client.force_login(self.lead_user)
        resp = self.client.get(reverse("student_detail", args=[self.student1.pk]))
        self.assertEqual(resp.status_code, 200)

        content = resp.content.decode()
        # Hero profile checks
        self.assertIn("Janey Doe", content)
        self.assertIn("Legal: Jane Doe", content)
        self.assertIn("Carnegie Mellon High", content)
        self.assertIn("Class of 2027", content)
        self.assertIn("mailto:janey@example.com", content)
        self.assertIn("tel:412-555-0101", content)

        # Section headers
        self.assertIn("Parents &amp; Emergency Contacts", content)
        self.assertIn("Contact &amp; Address", content)
        self.assertIn("Health &amp; Medical", content)
        self.assertIn("Peanuts", content)
        self.assertIn("Program Enrollments &amp; Teams", content)
        self.assertIn("Girls of Steel FRC", content)

    def test_adults_list_kpis_and_tabs(self):
        self.client.force_login(self.lead_user)
        resp = self.client.get(reverse("adult_list"))
        self.assertEqual(resp.status_code, 200)

        self.assertGreaterEqual(resp.context["total_adults_count"], 3)
        self.assertGreaterEqual(resp.context["parents_count"], 1)
        self.assertGreaterEqual(resp.context["mentors_count"], 1)
        self.assertGreaterEqual(resp.context["alumni_count"], 1)

        content = resp.content.decode()
        self.assertIn("Total Adults", content)
        self.assertIn("Parents / Guardians", content)
        self.assertIn("Mentors", content)
        self.assertIn("Alumni", content)
        self.assertIn("Mary Doe", content)
        self.assertIn("Sarah Alum", content)

    def test_adult_detail_view(self):
        self.client.force_login(self.lead_user)
        resp = self.client.get(reverse("adult_detail", args=[self.parent1.pk]))
        self.assertEqual(resp.status_code, 200)

        content = resp.content.decode()
        self.assertIn("Mary Doe", content)
        self.assertIn("Parent", content)
        self.assertIn("mailto:mary.doe@example.com", content)
        self.assertIn("Linked Students", content)
        self.assertIn("Janey Doe", content)

    def test_parent_mentor_alumni_specialized_lists(self):
        self.client.force_login(self.lead_user)

        # Parent List
        p_resp = self.client.get(reverse("parent_list"))
        self.assertEqual(p_resp.status_code, 200)
        self.assertEqual(p_resp.context["total_parents_count"], 1)
        self.assertEqual(p_resp.context["email_updates_count"], 1)
        p_content = p_resp.content.decode()
        self.assertIn("Mary Doe", p_content)
        self.assertIn("Janey Doe", p_content)

        # Mentor List
        m_resp = self.client.get(reverse("mentor_list"))
        self.assertEqual(m_resp.status_code, 200)
        self.assertEqual(m_resp.context["active_mentors_count"], 1)
        m_content = m_resp.content.decode()
        self.assertIn("Lead Mentor", m_content)

        # Alumni List
        a_resp = self.client.get(reverse("alumni_list"))
        self.assertEqual(a_resp.status_code, 200)
        self.assertEqual(a_resp.context["total_alumni_count"], 1)
        self.assertEqual(a_resp.context["ok_to_contact_count"], 1)
        a_content = a_resp.content.decode()
        self.assertIn("Sarah Alum", a_content)
        self.assertIn("MIT", a_content)
        self.assertIn("Robotics Corp", a_content)

    def test_student_sub_views(self):
        self.client.force_login(self.lead_user)

        # Emergency contacts
        em_resp = self.client.get(reverse("student_emergency_contacts"))
        self.assertEqual(em_resp.status_code, 200)
        em_content = em_resp.content.decode()
        self.assertIn("Janey Doe", em_content)
        self.assertIn("Mary Doe", em_content)

        # By Grade
        gr_resp = self.client.get(reverse("students_by_grade"))
        self.assertEqual(gr_resp.status_code, 200)
        gr_content = gr_resp.content.decode()
        self.assertIn("Janey Doe", gr_content)

        # By School
        sc_resp = self.client.get(reverse("students_by_school"))
        self.assertEqual(sc_resp.status_code, 200)
        sc_content = sc_resp.content.decode()
        self.assertIn("Carnegie Mellon High", sc_content)
        self.assertIn("Janey Doe", sc_content)

        # Photo Grid
        ph_resp = self.client.get(reverse("student_photos"))
        self.assertEqual(ph_resp.status_code, 200)
        ph_content = ph_resp.content.decode()
        self.assertIn("Janey Doe", ph_content)
