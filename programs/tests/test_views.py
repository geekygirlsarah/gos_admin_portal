from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User, Permission

from programs.models import Program, Student, Enrollment


class ViewTests(TestCase):
    def setUp(self):
        # Basic user
        self.user = User.objects.create_user(username='tester', password='pass12345')

    def test_program_list_requires_login(self):
        url = reverse('program_list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp.url)

    def test_program_student_add_enrolls_student(self):
        # Grant permission and login
        perm = Permission.objects.get(codename='change_student')
        self.user.user_permissions.add(perm)
        self.client.login(username='tester', password='pass12345')

        program = Program.objects.create(name='Robotics')
        student = Student.objects.create(legal_first_name='Alex', last_name='Smith')

        url = reverse('program_student_add', args=[program.pk])
        resp = self.client.post(url, {'student': student.pk})
        self.assertEqual(resp.status_code, 302)
        # Enrollment created
        self.assertTrue(Enrollment.objects.filter(student=student, program=program).exists())
        # Redirects to program detail
        self.assertEqual(resp.url, reverse('program_detail', args=[program.pk]))

    def test_student_create_view_creates_student(self):
        # Grant permission and login
        perm = Permission.objects.get(codename='add_student')
        self.user.user_permissions.add(perm)
        self.client.login(username='tester', password='pass12345')

        url = reverse('student_create')
        resp = self.client.post(url, {
            'legal_first_name': 'Jamie',
            'last_name': 'Lee',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('student_list'))
        self.assertTrue(Student.objects.filter(legal_first_name='Jamie', last_name='Lee').exists())

    def test_student_detail_view_renders(self):
        # Login without special permissions
        self.client.login(username='tester', password='pass12345')
        student = Student.objects.create(legal_first_name='Riley', last_name='Jones')
        url = reverse('student_detail', args=[student.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        # ensure context contains student
        self.assertIn('student', resp.context)
