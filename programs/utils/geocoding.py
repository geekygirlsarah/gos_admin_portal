"""Server-side address geocoding with a database-backed cache.

Student addresses are resolved to (latitude, longitude) coordinates via
OpenStreetMap's Nominatim geocoder. Results are cached in the
``AddressGeocode`` model keyed by a normalized address string so each unique
address is looked up (and counted against the geocoding service's usage
policy) only once; students who share an address reuse the same row.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, Iterable, Optional, Tuple

import requests
from django.conf import settings

from programs.models import AddressGeocode

logger = logging.getLogger(__name__)

LatLng = Tuple[float, float]


def normalize_address(address: Optional[str]) -> str:
    """Canonicalize an address for use as the geocode cache key."""
    if not address:
        return ""
    cleaned = str(address).replace(",", " ")
    return " ".join(cleaned.split()).strip().lower()


def _geocode_remote(address: str) -> Optional[LatLng]:
    """Ask the configured geocoding service for coordinates for an address."""
    params = {
        "format": "jsonv2",
        "limit": 1,
        "q": address,
    }
    url = getattr(
        settings,
        "GEOCODING_URL",
        "https://nominatim.openstreetmap.org/search",
    )
    timeout = getattr(settings, "GEOCODING_TIMEOUT", 10)
    headers = {
        "Accept": "application/json",
        "User-Agent": getattr(settings, "GEOCODING_USER_AGENT", "GoSAdminPortal/1.0"),
    }
    response = requests.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, list) and data:
        first = data[0]
        try:
            lat = float(first.get("lat"))
            lon = float(first.get("lon"))
        except (TypeError, ValueError):
            return None
        if lat and lon:
            return (lat, lon)
    return None


def _geocode_and_store(key: str, query: str) -> Optional[LatLng]:
    """Geocode an address and persist the result (or the miss) in the cache."""
    try:
        point = _geocode_remote(query)
    except requests.RequestException:
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
