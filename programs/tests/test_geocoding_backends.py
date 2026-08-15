from unittest import mock
from django.test import TestCase, override_settings
import requests
from programs.utils.geocoding import (
    NominatimBackend, 
    MapboxBackend, 
    _geocode_remote, 
    get_geocoding_backends
)

class MockResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"Error {self.status_code}")
        return None

    def json(self):
        return self._data

class GeocodingBackendTests(TestCase):
    def test_nominatim_backend_success(self):
        backend = NominatimBackend()
        with mock.patch("programs.utils.geocoding.requests.get") as mock_get:
            mock_get.return_value = MockResponse([{"lat": "40.44", "lon": "-79.99"}])
            result = backend.geocode("100 Main St")
            self.assertEqual(result, (40.44, -79.99))
            self.assertIn("nominatim.openstreetmap.org", mock_get.call_args[0][0])

    def test_nominatim_backend_failure(self):
        backend = NominatimBackend()
        with mock.patch("programs.utils.geocoding.requests.get") as mock_get:
            mock_get.return_value = MockResponse([])
            result = backend.geocode("Unknown Place")
            self.assertIsNone(result)

    @override_settings(MAPBOX_ACCESS_TOKEN="fake-token")
    def test_mapbox_backend_success(self):
        backend = MapboxBackend()
        with mock.patch("programs.utils.geocoding.requests.get") as mock_get:
            mock_get.return_value = MockResponse({
                "features": [{
                    "center": [-79.99, 40.44]
                }]
            })
            result = backend.geocode("100 Main St")
            self.assertEqual(result, (40.44, -79.99))
            self.assertIn("api.mapbox.com", mock_get.call_args[0][0])
            self.assertEqual(mock_get.call_args[1]["params"]["access_token"], "fake-token")

    @override_settings(MAPBOX_ACCESS_TOKEN="")
    def test_mapbox_backend_skipped_without_token(self):
        backend = MapboxBackend()
        with mock.patch("programs.utils.geocoding.requests.get") as mock_get:
            result = backend.geocode("100 Main St")
            self.assertIsNone(result)
            mock_get.assert_not_called()

    @override_settings(
        MAPBOX_ACCESS_TOKEN="fake-token",
        GEOCODING_BACKENDS=["programs.utils.geocoding.MapboxBackend", "programs.utils.geocoding.NominatimBackend"],
        GEOCODING_DELAY_SECONDS=0
    )
    def test_geocode_remote_fallbacks(self):
        # Scenario: Mapbox fails to find it, but Nominatim does
        def fake_geocode(url, *args, **kwargs):
            if "mapbox.com" in url:
                return MockResponse({"features": []})
            if "nominatim.openstreetmap.org" in url:
                return MockResponse([{"lat": "40.44", "lon": "-79.99"}])
            return MockResponse({}, status_code=404)

        with mock.patch("programs.utils.geocoding.requests.get", side_effect=fake_geocode) as mock_get:
            result = _geocode_remote("Fallback Street")
            self.assertEqual(result, (40.44, -79.99))
            self.assertEqual(mock_get.call_count, 2)

    @override_settings(
        MAPBOX_ACCESS_TOKEN="fake-token",
        GEOCODING_BACKENDS=["programs.utils.geocoding.MapboxBackend", "programs.utils.geocoding.NominatimBackend"],
        GEOCODING_DELAY_SECONDS=0
    )
    def test_geocode_remote_stops_at_first_success(self):
        # Scenario: Mapbox finds it, Nominatim should not be called
        with mock.patch("programs.utils.geocoding.requests.get") as mock_get:
            mock_get.return_value = MockResponse({
                "features": [{
                    "center": [-79.99, 40.44]
                }]
            })
            result = _geocode_remote("Mapbox Street")
            self.assertEqual(result, (40.44, -79.99))
            self.assertEqual(mock_get.call_count, 1)
            self.assertIn("mapbox.com", mock_get.call_args[0][0])
