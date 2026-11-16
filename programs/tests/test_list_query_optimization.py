"""Regression tests for N+1 query avoidance in list and detail views.

List views should prefetch/select_related the related records they render so
the number of SQL queries stays bounded regardless of how many students,
parents, mentors, or alumni are on the page. Detail views should prefetch
their related records too so a single page doesn't issue a query per related
row.
"""

from datetime import date

from django.contrib.auth.models import User
from django.db import connection
from django.template.loader import render_to_string
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from organizations.models import Organization, OrganizationDomain
from organizations.utils import get_organization_email_settings
from programs.models import (
    Adult,
    AdultStudentRelationship,
    BackgroundCheck,
    BackgroundCheckType,
    Crew,
    Enrollment,
    Program,
    RaceEthnicity,
    School,
    Student,
)


def _count_queries(func):
    with CaptureQueriesContext(connection) as ctx:
        func()
    return len(ctx.captured_queries)


@override_settings(MOMENTUM_ROOT_DOMAIN="momentumteam.app")
class EmailSettingsQueryOptimizationTest(TestCase):
    def setUp(self):
        self.first = Organization.objects.create(
            name="Alpha", slug="alpha", subdomain="alpha"
        )
        self.second = Organization.objects.create(
            name="Beta", slug="beta", subdomain="beta"
        )
        operator = User.objects.create_user(username="domain_operator")
        self.domain = OrganizationDomain.objects.create(
            organization=self.first,
            hostname="alpha.example.org",
            status=OrganizationDomain.STATUS_ACTIVE,
            dns_verified_at=timezone.now(),
            activated_at=timezone.now(),
            activated_by=operator,
        )

    def test_contact_settings_do_not_resolve_unused_portal_url(self):
        with self.assertNumQueries(1):
            config = get_organization_email_settings(self.first)
            self.assertTrue(config["contact_email"])

    def test_portal_url_stays_fresh_and_fails_closed_for_conflicting_claims(self):
        first_url = get_organization_email_settings(self.first)["portal_url"]
        second_url = get_organization_email_settings(self.second)["portal_url"]
        self.assertEqual(str(first_url), "https://alpha.example.org")
        self.assertEqual(str(second_url), "https://beta.momentumteam.app")

        self.domain.status = OrganizationDomain.STATUS_DISABLED
        self.domain.save(update_fields=["status"])
        self.assertEqual(str(first_url), "https://alpha.momentumteam.app")

        self.domain.status = OrganizationDomain.STATUS_ACTIVE
        self.domain.save(update_fields=["status"])
        self.second.settings = {"custom_domain": "alpha.example.org"}
        self.second.save(update_fields=["settings"])
        self.assertEqual(str(first_url), "https://alpha.momentumteam.app")
        self.assertEqual(str(second_url), "https://beta.momentumteam.app")

        self.domain.status = OrganizationDomain.STATUS_DISABLED
        self.domain.save(update_fields=["status"])
        self.second.settings = {"custom_domain": "alpha.momentumteam.app"}
        self.second.save(update_fields=["settings"])
        self.assertEqual(str(first_url), "")
        self.assertEqual(str(second_url), "https://beta.momentumteam.app")
        rendered = render_to_string(
            "programs/emails/base_notification.html",
            {
                "org_email_settings": {
                    "organization_name": "Alpha",
                    "portal_url": first_url,
                }
            },
        )
        self.assertNotIn('href="https://alpha', rendered)

    def test_email_template_renders_each_organizations_canonical_url(self):
        for organization, expected_url in (
            (self.first, "https://alpha.example.org"),
            (self.second, "https://beta.momentumteam.app"),
        ):
            with self.subTest(organization=organization.slug):
                rendered = render_to_string(
                    "programs/emails/base_notification.html",
                    {
                        "org_email_settings": get_organization_email_settings(
                            organization
                        )
                    },
                )
                self.assertIn(f'href="{expected_url}"', rendered)


class ListViewQueryOptimizationTest(TestCase):
    NUM = 10

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="admin",
            password="password",
            email="admin@example.com",  # nosec B106
        )
        self.client = Client()
        self.client.force_login(self.user)

        programs = [Program.objects.create(name=f"Program {i}") for i in range(3)]
        schools = [School.objects.create(name=f"School {i}") for i in range(self.NUM)]

        self.students = []
        for i in range(self.NUM):
            student = Student.objects.create(
                legal_first_name=f"Student{i}",
                last_name="Zebra",
                school=schools[i],
            )
            # Two enrollments per student to stress the reverse FK prefetch.
            Enrollment.objects.create(student=student, program=programs[0])
            Enrollment.objects.create(student=student, program=programs[1])
            self.students.append(student)

        # Link the same two adults to every student as primary/secondary.
        self.adults = [
            Adult.objects.create(
                legal_first_name=f"Parent{i}",
                last_name="Doe",
                is_parent=True,
            )
            for i in range(2)
        ]
        for student in self.students:
            for adult in self.adults:
                AdultStudentRelationship.objects.create(adult=adult, student=student)
            student.primary_contact_relationship = AdultStudentRelationship.objects.get(
                adult=self.adults[0], student=student
            )
            student.secondary_contact_relationship = (
                AdultStudentRelationship.objects.get(
                    adult=self.adults[1], student=student
                )
            )
            student.save()

        # Mentors/alumni: each linked to two students.
        self.mentors = [
            Adult.objects.create(
                legal_first_name=f"Mentor{i}", last_name="Doe", is_mentor=True
            )
            for i in range(self.NUM)
        ]
        self.alumni = [
            Adult.objects.create(
                legal_first_name=f"Alum{i}", last_name="Doe", is_alumni=True
            )
            for i in range(self.NUM)
        ]
        for i, mentor in enumerate(self.mentors):
            AdultStudentRelationship.objects.create(
                adult=mentor,
                student=self.students[i % self.NUM],
            )
            AdultStudentRelationship.objects.create(
                adult=mentor,
                student=self.students[(i + 1) % self.NUM],
            )
        for i, alum in enumerate(self.alumni):
            AdultStudentRelationship.objects.create(
                adult=alum,
                student=self.students[i % self.NUM],
            )
            AdultStudentRelationship.objects.create(
                adult=alum,
                student=self.students[(i + 1) % self.NUM],
            )

    def test_student_list_query_count_is_bounded(self):
        # Without prefetching this renders ~8+ queries per student.
        # With prefetch/select_related it stays around a handful.
        def fetch():
            return self.client.get(reverse("student_list"))

        response = fetch()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["students"]), self.NUM)
        self.assertLess(
            _count_queries(fetch),
            13,  # fixed overhead includes the scoped Group prefetch
            "student_list is performing an N+1 query pattern",
        )

    def test_parent_list_query_count_is_bounded(self):
        def fetch():
            return self.client.get(reverse("parent_list"))

        response = fetch()
        self.assertEqual(response.status_code, 200)
        self.assertLess(
            _count_queries(fetch),
            11,  # fixed overhead includes the scoped Group prefetch
            "parent_list is performing an N+1 query pattern",
        )

    def test_mentor_list_query_count_is_bounded(self):
        def fetch():
            return self.client.get(reverse("mentor_list"))

        response = fetch()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["mentors"]), self.NUM)
        self.assertLess(
            _count_queries(fetch),
            10,
            "mentor_list is performing an N+1 query pattern",
        )

    def test_alumni_list_query_count_is_bounded(self):
        def fetch():
            return self.client.get(reverse("alumni_list"))

        response = fetch()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["alumni"]), self.NUM)
        self.assertLess(
            _count_queries(fetch),
            11,
            "alumni_list is performing an N+1 query pattern",
        )


class ProgramPhotoGridQueryOptimizationTest(TestCase):
    NUM = 10

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="admin",
            password="password",
            email="admin@example.com",  # nosec B106
        )
        self.client = Client()
        self.client.force_login(self.user)

        self.program = Program.objects.create(name="Photo Program")
        self.crew = Crew.objects.create(name="Crew A", program=self.program)
        for i in range(self.NUM):
            student = Student.objects.create(
                legal_first_name=f"Student{i}", last_name="Zebra"
            )
            Enrollment.objects.create(
                student=student, program=self.program, crew=self.crew
            )

    def test_program_photo_grid_query_count_is_bounded(self):
        # Each card renders the enrollment's crew, so it must be select_related.
        def fetch():
            return self.client.get(
                reverse("program_student_photos", args=[self.program.pk])
            )

        response = fetch()
        self.assertEqual(response.status_code, 200)
        self.assertLess(
            _count_queries(fetch),
            13,  # fixed overhead includes the scoped Group prefetch
            "program_student_photos is performing an N+1 query pattern",
        )


class StudentDetailQueryOptimizationTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="admin",
            password="password",
            email="admin@example.com",  # nosec B106
        )
        self.client = Client()
        self.client.force_login(self.user)

        school = School.objects.create(name="High School")
        parent1 = Adult.objects.create(
            legal_first_name="Mom", last_name="Doe", is_parent=True
        )
        parent2 = Adult.objects.create(
            legal_first_name="Dad", last_name="Doe", is_parent=True
        )
        self.student = Student.objects.create(
            preferred_first_name="Jane", last_name="Doe", school=school
        )
        AdultStudentRelationship.objects.create(adult=parent1, student=self.student)
        AdultStudentRelationship.objects.create(adult=parent2, student=self.student)
        self.student.primary_contact_relationship = (
            AdultStudentRelationship.objects.get(adult=parent1, student=self.student)
        )
        self.student.secondary_contact_relationship = (
            AdultStudentRelationship.objects.get(adult=parent2, student=self.student)
        )
        self.student.save()

        for check_type in BackgroundCheckType.objects.filter(is_active=True):
            BackgroundCheck.objects.create(
                student=self.student,
                check_type=check_type,
                cleared=True,
                obtained_date=date(2026, 1, 1),
            )
        ethnicity = RaceEthnicity.objects.create(key="test", name="Test")
        self.student.race_ethnicities.add(ethnicity)
        program = Program.objects.create(name="Program A")
        Enrollment.objects.create(student=self.student, program=program)

    def test_student_detail_query_count_is_bounded(self):
        def fetch():
            return self.client.get(reverse("student_detail", args=[self.student.pk]))

        response = fetch()
        self.assertEqual(response.status_code, 200)
        self.assertLess(
            _count_queries(fetch),
            19,  # fixed overhead includes the scoped Group prefetch
            "student_detail is performing an N+1 query pattern",
        )


class AdultDetailQueryOptimizationTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="admin",
            password="password",
            email="admin@example.com",  # nosec B106
        )
        self.client = Client()
        self.client.force_login(self.user)

        self.adult = Adult.objects.create(
            legal_first_name="Mom", last_name="Doe", is_parent=True
        )
        for i in range(3):
            student = Student.objects.create(
                legal_first_name=f"Student{i}", last_name="Zebra"
            )
            AdultStudentRelationship.objects.create(adult=self.adult, student=student)
        for check_type in BackgroundCheckType.objects.filter(is_active=True):
            BackgroundCheck.objects.create(
                adult=self.adult,
                check_type=check_type,
                cleared=True,
                obtained_date=date(2026, 1, 1),
            )

    def test_adult_detail_query_count_is_bounded(self):
        def fetch():
            return self.client.get(reverse("adult_detail", args=[self.adult.pk]))

        response = fetch()
        self.assertEqual(response.status_code, 200)
        self.assertLess(
            _count_queries(fetch),
            16,  # fixed overhead incl. the adult's organization lookup
            "adult_detail is performing an N+1 query pattern",
        )
