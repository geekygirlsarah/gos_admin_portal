"""Server-side address geocoding with a database-backed cache.

Student addresses are resolved to (latitude, longitude) coordinates via
configurable geocoding backends (Mapbox, Nominatim/OSM). Results are cached
in the ``AddressGeocode`` model keyed by a normalized address string so each
unique address is looked up (and counted against the geocoding service's usage
policy) only once; students who share an address reuse the same row.
"""

from __future__ import annotations

import logging
import time
import urllib.parse
from typing import Dict, Iterable, List, Optional, Tuple

import requests
from django.conf import settings
from django.utils.module_loading import import_string

from programs.models import AddressGeocode

logger = logging.getLogger(__name__)

LatLng = Tuple[float, float]


class BaseGeocodingBackend:
    """Base class for geocoding service backends."""

    def __init__(self):
        self.timeout = getattr(settings, "GEOCODING_TIMEOUT", 10)
        self.user_agent = getattr(
            settings, "GEOCODING_USER_AGENT", "GoSAdminPortal/1.0"
        )
        self.delay = getattr(settings, "GEOCODING_DELAY_SECONDS", 1.0)

    def geocode(self, address: str) -> Optional[LatLng]:
        """Ask the geocoding service for coordinates for an address."""
        raise NotImplementedError

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }


class NominatimBackend(BaseGeocodingBackend):
    """Geocoding backend using OpenStreetMap's Nominatim service."""

    def geocode(self, address: str) -> Optional[LatLng]:
        url = getattr(
            settings,
            "GEOCODING_URL",
            "https://nominatim.openstreetmap.org/search",
        )
        params = {
            "format": "jsonv2",
            "limit": 1,
            "q": address,
        }
        try:
            response = requests.get(
                url, params=params, headers=self._get_headers(), timeout=self.timeout
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list) and data:
                first = data[0]
                lat = float(first.get("lat"))
                lon = float(first.get("lon"))
                if lat is not None and lon is not None:
                    return (lat, lon)
        except (requests.RequestException, TypeError, ValueError, KeyError):
            pass
        return None


class MapboxBackend(BaseGeocodingBackend):
    """Geocoding backend using Mapbox Geocoding API."""

    def geocode(self, address: str) -> Optional[LatLng]:
        token = getattr(settings, "MAPBOX_ACCESS_TOKEN", None)
        if not token:
            logger.debug("Mapbox backend skipped: MAPBOX_ACCESS_TOKEN not set.")
            return None

        # Mapbox expects the address in the URL path.
        quoted_address = urllib.parse.quote(address)
        url = f"https://api.mapbox.com/geocoding/v5/mapbox.places/{quoted_address}.json"
        params = {
            "access_token": token,
            "limit": 1,
        }
        try:
            response = requests.get(
                url, params=params, headers=self._get_headers(), timeout=self.timeout
            )
            response.raise_for_status()
            data = response.json()
            features = data.get("features", [])
            if features:
                # Mapbox returns [longitude, latitude] in the "center" field.
                center = features[0].get("center")
                if center and len(center) == 2:
                    lat, lon = float(center[1]), float(center[0])
                    if lat is not None and lon is not None:
                        return (lat, lon)
        except (requests.RequestException, TypeError, ValueError, KeyError):
            pass
        return None


def get_geocoding_backends() -> List[BaseGeocodingBackend]:
    """Instantiate the geocoding backends configured in settings."""
    backend_paths = getattr(
        settings,
        "GEOCODING_BACKENDS",
        [
            "programs.utils.geocoding.MapboxBackend",
            "programs.utils.geocoding.NominatimBackend",
        ],
    )
    backends = []
    for path in backend_paths:
        try:
            backend_class = import_string(path)
            backends.append(backend_class())
        except ImportError:
            logger.error("Could not import geocoding backend: %s", path)
    return backends


def normalize_address(address: Optional[str]) -> str:
    """Canonicalize an address for use as the geocode cache key."""
    if not address:
        return ""
    cleaned = str(address).replace(",", " ")
    return " ".join(cleaned.split()).strip().lower()


def _geocode_remote(address: str) -> Optional[LatLng]:
    """Ask configured geocoding backends for coordinates, with fallbacks."""
    backends = get_geocoding_backends()
    for backend in backends:
        point = backend.geocode(address)
        if point:
            return point

        # If we have multiple backends, be polite and wait between calls if configured
        if len(backends) > 1 and backend.delay:
            time.sleep(backend.delay)

    return None


def _geocode_and_store(key: str, query: str) -> Optional[LatLng]:
    """Geocode an address and persist the result (or the miss) in the cache."""
    try:
        point = _geocode_remote(query)
    except Exception:
        logger.warning("Address geocoding lookup failed for %r", query, exc_info=True)
        point = None
    else:
        # Be polite to the service: Nominatim asks for at most 1 req/s.
        delay = getattr(settings, "GEOCODING_DELAY_SECONDS", 1.0)
        if delay:
            time.sleep(delay)
    AddressGeocode.objects.update_or_create(
        address=key,
        defaults={
            "latitude": point[0] if point else None,
            "longitude": point[1] if point else None,
            "found": point is not None,
        },
    )
    return point


def resolve_address_points(
    addresses: Iterable[Optional[str]],
) -> Dict[str, Optional[LatLng]]:
    """Resolve addresses to coordinates, using the DB cache and geocoding the
    rest. Returns a dict keyed by the exact address strings that were passed
    in (blank/missing addresses map to ``None`` and never hit the service).
    """
    result: Dict[str, Optional[LatLng]] = {}
    by_key: Dict[str, str] = {}
    for raw in addresses:
        key = normalize_address(raw)
        result[raw] = None
        if key:
            by_key.setdefault(key, raw)

    keys = list(by_key)
    if not keys:
        return result

    cached = {g.address: g for g in AddressGeocode.objects.filter(address__in=keys)}
    points: Dict[str, Optional[LatLng]] = {}
    for key in keys:
        entry = cached.get(key)
        if entry is None:
            points[key] = _geocode_and_store(key, by_key[key])
        elif entry.latitude is not None and entry.longitude is not None:
            points[key] = (entry.latitude, entry.longitude)
        else:
            # Previously attempted and not found; don't ask again.
            points[key] = None

    for raw, key in ((r, normalize_address(r)) for r in addresses):
        if key in points:
            result[raw] = points[key]
    return result
