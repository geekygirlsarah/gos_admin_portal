from unittest import mock

import requests
from django.core.management import call_command
from django.test import TestCase, override_settings

from programs.models import AddressGeocode, Enrollment, Program, Student
from programs.utils import normalize_address, resolve_address_points


class NormalizeAddressTests(TestCase):
    def test_collapses_whitespace_and_lowercases(self):
        self.assertEqual(normalize_address("  100  Main   ST. "), "100 main st.")

    def test_removes_commas(self):
        self.assertEqual(
            normalize_address("100 Main St., Pittsburgh, PA 15213"),
            "100 main st. pittsburgh pa 15213",
        )

    def test_empty_and_none(self):
        self.assertEqual(normalize_address(""), "")
        self.assertEqual(normalize_address(None), "")


class MockResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


@override_settings(GEOCODING_DELAY_SECONDS=0)
class ResolveAddressPointsTests(TestCase):
    def test_returns_cached_points_without_remote_call(self):
        AddressGeocode.objects.create(
            address="100 main st. pittsburgh pa 15213",
            latitude=40.44,
            longitude=-79.99,
            found=True,
        )
        with mock.patch(
            "programs.utils.geocoding.requests.get",
            side_effect=AssertionError("should not call the geocoding service"),
        ):
            result = resolve_address_points(["100 Main St., Pittsburgh PA 15213"])
        self.assertEqual(result, {"100 Main St., Pittsburgh PA 15213": (40.44, -79.99)})

    def test_geocodes_missing_addresses_and_stores_them(self):
        def fake_get(url, params, headers, timeout):
            self.assertEqual(params["q"], "100 Main St., Pittsburgh PA 15213")
            return MockResponse([{"lat": "40.44", "lon": "-79.99"}])

        with mock.patch(
            "programs.utils.geocoding.requests.get", side_effect=fake_get
        ) as mock_get:
            result = resolve_address_points(["100 Main St., Pittsburgh PA 15213"])

        self.assertEqual(result, {"100 Main St., Pittsburgh PA 15213": (40.44, -79.99)})
        mock_get.assert_called_once()
        entry = AddressGeocode.objects.get(address="100 main st. pittsburgh pa 15213")
        self.assertTrue(entry.found)
        self.assertEqual(entry.latitude, 40.44)
        self.assertEqual(entry.longitude, -79.99)

    def test_geocodes_only_once_for_identical_addresses(self):
        with mock.patch(
            "programs.utils.geocoding.requests.get",
            side_effect=lambda *a, **k: MockResponse(
                [{"lat": "40.44", "lon": "-79.99"}]
            ),
        ) as mock_get:
            result = resolve_address_points(
                [
                    "100 Main St., Pittsburgh, PA 15213",
                    "100 Main St. Pittsburgh PA 15213",
                    " 100  Main   St., Pittsburgh PA 15213",
                ]
            )

        mock_get.assert_called_once()
        self.assertEqual(
            set(result.values()),
            {(40.44, -79.99)},
        )

    def test_unresolved_addresses_are_cached_as_not_found(self):
        with mock.patch(
            "programs.utils.geocoding.requests.get",
            side_effect=lambda *a, **k: MockResponse([]),
        ) as mock_get:
            result = resolve_address_points(["404 Nowhere Ave, Nowhere PA"])
            mock_get.assert_called_once()
            self.assertEqual(result, {"404 Nowhere Ave, Nowhere PA": None})

        # Second lookup must not hit the service again.
        with mock.patch(
            "programs.utils.geocoding.requests.get",
            side_effect=AssertionError("should not be called again"),
        ):
            result = resolve_address_points(["404 Nowhere Ave, Nowhere PA"])
        self.assertEqual(result, {"404 Nowhere Ave, Nowhere PA": None})
        entry = AddressGeocode.objects.get(address="404 nowhere ave nowhere pa")
        self.assertFalse(entry.found)

    def test_network_errors_are_swallowed_and_cached(self):
        with mock.patch(
            "programs.utils.geocoding.requests.get",
            side_effect=requests.ConnectionError("boom"),
        ):
            result = resolve_address_points(["100 Main St., Pittsburgh PA 15213"])

        self.assertEqual(result, {"100 Main St., Pittsburgh PA 15213": None})
        entry = AddressGeocode.objects.get(address="100 main st. pittsburgh pa 15213")
        self.assertFalse(entry.found)

    def test_blank_addresses_return_none_without_calls(self):
        with mock.patch(
            "programs.utils.geocoding.requests.get",
            side_effect=AssertionError("should not be called"),
        ):
            result = resolve_address_points(["", None])
        self.assertEqual(result, {"": None, None: None})


@override_settings(GEOCODING_DELAY_SECONDS=0)
class GeocodeStudentAddressesCommandTests(TestCase):
    def test_command_geocodes_unique_student_addresses_and_caches(self):
        program = Program.objects.create(name="Robot Camp")
        student = Student.objects.create(
            legal_first_name="Ada",
            last_name="Lovelace",
            address="100 Main St.",
            city="Pittsburgh",
            state="PA",
            zip_code="15213",
        )
        Enrollment.objects.create(student=student, program=program)

        with mock.patch(
            "programs.utils.geocoding.requests.get",
            side_effect=lambda *a, **k: MockResponse(
                [{"lat": "40.44", "lon": "-79.99"}]
            ),
        ) as mock_get:
            call_command("geocode_student_addresses", "--program", program.pk)

        self.assertEqual(mock_get.call_count, 1)
        self.assertTrue(AddressGeocode.objects.filter(found=True).exists())

        # Running again reuses the cache.
        with mock.patch(
            "programs.utils.geocoding.requests.get",
            side_effect=AssertionError("should not be called again"),
        ):
            call_command("geocode_student_addresses", "--program", program.pk)

    def test_command_dry_run_makes_no_requests(self):
        program = Program.objects.create(name="Robot Camp")
        student = Student.objects.create(
            legal_first_name="Ada",
            last_name="Lovelace",
            address="100 Main St.",
            city="Pittsburgh",
            state="PA",
            zip_code="15213",
        )
        Enrollment.objects.create(student=student, program=program)

        with mock.patch(
            "programs.utils.geocoding.requests.get",
            side_effect=AssertionError("dry run must not geocode"),
        ):
            call_command(
                "geocode_student_addresses", "--program", program.pk, "--dry-run"
            )
        self.assertFalse(AddressGeocode.objects.exists())
