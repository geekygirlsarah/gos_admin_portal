from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse

from programs.models import (
    AddressGeocode,
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Program,
    RolePermission,
    Student,
)


class MockResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


def fake_geocode(*args, **kwargs):
    return MockResponse([{"lat": "40.44", "lon": "-79.99"}])


@override_settings(GEOCODING_DELAY_SECONDS=0)
class StudentMapViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mentor", password="pass12345"
        )  # nosec B106
        group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(group)
        self.client.login(username="mentor", password="pass12345")  # nosec B106

        self.program = Program.objects.create(name="Robot Camp", active=True)
        self.url = reverse("program_student_map", args=[self.program.pk])

    def _enroll(self, student):
        Enrollment.objects.create(student=student, program=self.program)

    def _student(self, name, consent=True, phone=None, email=None):
        first, last = name.split(" ", 1)
        return Student.objects.create(
            legal_first_name=first,
            last_name=last,
            address="100 Main St.",
            city="Pittsburgh",
            state="PA",
            zip_code="15213",
            directory_consent=consent,
            phone_number=phone,
            personal_email=email,
        )

    def test_map_view_geocodes_and_caches_student_points(self):
        student = self._student("Ada Lovelace")
        self._enroll(student)

        with mock.patch(
            "programs.utils.geocoding.requests.get", side_effect=fake_geocode
        ):
            resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Ada Lovelace")
        self.assertContains(resp, '"latitude": 40.44')
        self.assertContains(resp, '"longitude": -79.99')
        self.assertTrue(AddressGeocode.objects.filter(found=True).exists())

        # Second load must use the DB cache without hitting the service.
        with mock.patch(
            "programs.utils.geocoding.requests.get",
            side_effect=AssertionError("should not call the service again"),
        ):
            resp2 = self.client.get(self.url)
        self.assertEqual(resp2.status_code, 200)
        self.assertContains(resp2, '"latitude": 40.44')

    def test_map_view_uses_server_side_points_not_client_geocoder(self):
        student = self._student("Grace Hopper")
        self._enroll(student)

        with mock.patch(
            "programs.utils.geocoding.requests.get", side_effect=fake_geocode
        ):
            resp = self.client.get(self.url)

        # The template should no longer geocode in the browser.
        self.assertNotContains(resp, "nominatim.openstreetmap.org/search")
        self.assertNotContains(resp, "queue.shift")
        self.assertNotContains(resp, "setTimeout(step, 1100)")

    def test_map_view_fits_bounds_to_students(self):
        student = self._student("Ada Lovelace")
        self._enroll(student)

        with mock.patch(
            "programs.utils.geocoding.requests.get", side_effect=fake_geocode
        ):
            resp = self.client.get(self.url)

        self.assertContains(resp, "fitBounds")

    def test_map_view_skips_students_without_addresses(self):
        student = Student.objects.create(
            legal_first_name="No",
            last_name="Address",
            city=None,
            state=None,
            zip_code=None,
            address=None,
        )
        self._enroll(student)

        with mock.patch(
            "programs.utils.geocoding.requests.get",
            side_effect=AssertionError("no addresses to geocode"),
        ):
            resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "No Address")

    def test_map_view_links_back_to_program_for_mentors(self):
        with mock.patch(
            "programs.utils.geocoding.requests.get",
            side_effect=AssertionError("no students to geocode"),
        ):
            resp = self.client.get(self.url)

        self.assertContains(resp, "Back to Program")
        self.assertContains(resp, reverse("program_detail", args=[self.program.pk]))

    def test_map_view_renders_empty_state_without_students(self):
        with mock.patch(
            "programs.utils.geocoding.requests.get",
            side_effect=AssertionError("no students to geocode"),
        ):
            resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No student addresses available to map")

    def test_lead_mentor_sees_all_students_regardless_of_consent(self):
        consenting = self._student("Consent Yes")
        non_consenting = self._student("Consent No", consent=False)
        self._enroll(consenting)
        self._enroll(non_consenting)

        with mock.patch(
            "programs.utils.geocoding.requests.get", side_effect=fake_geocode
        ):
            resp = self.client.get(self.url)

        self.assertContains(resp, "Consent Yes")
        self.assertContains(resp, "Consent No")

    def test_marker_popup_includes_phone_and_email(self):
        student = self._student(
            "Ada Lovelace", phone="4125551234", email="ada@example.com"
        )
        self._enroll(student)

        with mock.patch(
            "programs.utils.geocoding.requests.get", side_effect=fake_geocode
        ):
            resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "4125551234")
        self.assertContains(resp, "ada@example.com")

    def test_marker_popup_works_without_phone_or_email(self):
        student = self._student("No Contact")
        self._enroll(student)

        with mock.patch(
            "programs.utils.geocoding.requests.get", side_effect=fake_geocode
        ):
            resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No Contact")

    def test_lead_mentor_does_not_see_unlisted_section(self):
        self._student("Consent Yes")
        self._student("Consent No", consent=False)

        with mock.patch(
            "programs.utils.geocoding.requests.get", side_effect=fake_geocode
        ):
            resp = self.client.get(self.url)

        self.assertNotContains(resp, "Unlisted Students")


class ParentConsentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="parent", password="pass12345"
        )  # nosec B106
        self.adult = Adult.objects.create(
            user=self.user, first_name="Parent", last_name="One", is_parent=True
        )
        self.client.login(username="parent", password="pass12345")  # nosec B106

        self.program = Program.objects.create(name="Robot Camp", active=True)
        self.url = reverse("program_student_map", args=[self.program.pk])

    def _enroll(self, student):
        Enrollment.objects.create(student=student, program=self.program)
        AdultStudentRelationship.objects.create(adult=self.adult, student=student)

    def test_parent_only_sees_consenting_students_on_carpool_map(self):
        consenting = Student.objects.create(
            legal_first_name="Consent",
            last_name="Yes",
            address="100 Main St.",
            city="Pittsburgh",
            state="PA",
            zip_code="15213",
            directory_consent=True,
        )
        non_consenting = Student.objects.create(
            legal_first_name="Consent",
            last_name="No",
            address="200 Second Ave.",
            city="Pittsburgh",
            state="PA",
            zip_code="15213",
            directory_consent=False,
        )
        self._enroll(consenting)
        self._enroll(non_consenting)

        with mock.patch(
            "programs.utils.geocoding.requests.get", side_effect=fake_geocode
        ) as mock_get:
            resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Consent Yes")
        # "Consent No" should NOT appear in the JSON map data, only in the
        # unlisted section.
        import html as html_mod
        import json

        script_start = resp.content.decode().index('id="student-items"')
        script_tag = resp.content.decode()[
            script_start : resp.content.decode().index("</script>", script_start)
        ]
        json_str = script_tag.split(">", 1)[1]
        items = json.loads(json_str)
        item_names = [i["name"] for i in items]
        self.assertIn("Consent Yes", item_names)
        self.assertNotIn("Consent No", item_names)
        # The name does appear in the unlisted section though.
        self.assertContains(resp, "Consent No")
        # Only one address is looked up for this parent's view.
        self.assertEqual(mock_get.call_count, 1)

    def test_parent_sees_unlisted_students_section_for_non_consenter(self):
        consenting = Student.objects.create(
            legal_first_name="Consent",
            last_name="Yes",
            address="100 Main St.",
            city="Pittsburgh",
            state="PA",
            zip_code="15213",
            directory_consent=True,
        )
        non_consenting = Student.objects.create(
            legal_first_name="Consent",
            last_name="No",
            address="200 Second Ave.",
            city="Pittsburgh",
            state="PA",
            zip_code="15213",
            directory_consent=False,
        )
        self._enroll(consenting)
        self._enroll(non_consenting)

        with mock.patch(
            "programs.utils.geocoding.requests.get", side_effect=fake_geocode
        ):
            resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Unlisted Students")
        self.assertContains(resp, "Consent No")
        # Consenting student is on the map, not in the unlisted section.
        self.assertContains(resp, "Consent Yes")

    def test_parent_unlisted_section_hidden_when_all_consent(self):
        self._enroll(self._student("All Consent"))

        with mock.patch(
            "programs.utils.geocoding.requests.get", side_effect=fake_geocode
        ):
            resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Unlisted Students")

    def test_parent_marker_popup_includes_phone_and_email(self):
        student = self._student("Contact Me")
        student.phone_number = "4125559999"
        student.personal_email = "contact@example.com"
        student.save()
        self._enroll(student)

        with mock.patch(
            "programs.utils.geocoding.requests.get", side_effect=fake_geocode
        ):
            resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "4125559999")
        self.assertContains(resp, "contact@example.com")

    def test_parent_map_links_back_to_dashboard(self):
        self._enroll(self._student("Consent Yes"))

        with mock.patch(
            "programs.utils.geocoding.requests.get", side_effect=fake_geocode
        ):
            resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Back to Dashboard")
        self.assertContains(resp, reverse("profile_dashboard"))

    def _student(self, name):
        first, last = name.split(" ", 1)
        return Student.objects.create(
            legal_first_name=first,
            last_name=last,
            address="100 Main St.",
            city="Pittsburgh",
            state="PA",
            zip_code="15213",
            directory_consent=True,
        )


@override_settings(GEOCODING_DELAY_SECONDS=0)
class CarpoolButtonTests(TestCase):
    def test_program_detail_shows_map_view_for_lead_mentor(self):
        user = User.objects.create_user(
            username="mentor", password="pass12345"
        )  # nosec B106
        group, _ = Group.objects.get_or_create(name="LeadMentor")
        user.groups.add(group)
        self.client.login(username="mentor", password="pass12345")  # nosec B106

        program = Program.objects.create(name="Robot Camp")
        url = reverse("program_detail", args=[program.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Map View")
        self.assertNotContains(resp, "Carpool Map")

    def test_parent_dashboard_shows_carpool_map_button(self):
        user = User.objects.create_user(
            username="parent", password="pass12345"
        )  # nosec B106
        adult = Adult.objects.create(
            user=user, first_name="Parent", last_name="One", is_parent=True
        )
        program = Program.objects.create(name="Robot Camp", active=True)
        student = Student.objects.create(legal_first_name="Kid", last_name="One")
        Enrollment.objects.create(student=student, program=program)
        AdultStudentRelationship.objects.create(adult=adult, student=student)

        self.client.login(username="parent", password="pass12345")  # nosec B106
        resp = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Carpool Map")
        self.assertContains(resp, reverse("program_student_map", args=[program.pk]))

    def test_student_dashboard_shows_carpool_map_button(self):
        user = User.objects.create_user(
            username="student", password="pass12345"
        )  # nosec B106
        program = Program.objects.create(name="Robot Camp", active=True)
        student = Student.objects.create(
            legal_first_name="Kid", last_name="One", user=user
        )
        Enrollment.objects.create(student=student, program=program)

        self.client.login(username="student", password="pass12345")  # nosec B106
        resp = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Carpool Map")
        self.assertContains(resp, reverse("program_student_map", args=[program.pk]))


@override_settings(GEOCODING_DELAY_SECONDS=0)
class ParentCarpoolPermissionTests(TestCase):
    """Parents must always be able to access the carpool map regardless of
    RolePermission configuration for the programs section."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="parent", password="pass12345"
        )  # nosec B106
        self.adult = Adult.objects.create(
            user=self.user, first_name="Parent", last_name="One", is_parent=True
        )
        self.program = Program.objects.create(name="Robot Camp", active=True)
        self.url = reverse("program_student_map", args=[self.program.pk])
        self.client.login(username="parent", password="pass12345")  # nosec B106

    def test_parent_can_access_carpool_map_when_programs_read_denied(self):
        RolePermission.objects.create(
            role="Parent", section="programs", can_read=False, can_write=False
        )
        student = Student.objects.create(
            legal_first_name="Kid",
            last_name="One",
            address="100 Main St.",
            city="Pittsburgh",
            state="PA",
            zip_code="15213",
            directory_consent=True,
        )
        Enrollment.objects.create(student=student, program=self.program)
        AdultStudentRelationship.objects.create(adult=self.adult, student=student)

        with mock.patch(
            "programs.utils.geocoding.requests.get", side_effect=fake_geocode
        ):
            resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 200)
