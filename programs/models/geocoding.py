from django.db import models


class AddressGeocode(models.Model):
    """Cache of geocoded addresses used by the student map view.

    Keyed by a normalized address string so each unique address is looked up
    (and counted against the geocoding service's usage policy) only once.
    Students who share an address (e.g. siblings) reuse the same row.
    """

    address = models.CharField(
        max_length=512,
        unique=True,
        db_index=True,
        help_text="Normalized address string used as the cache key.",
    )
    latitude = models.FloatField(
        null=True,
        blank=True,
        help_text="Latitude of the address, or null if it could not be geocoded.",
    )
    longitude = models.FloatField(
        null=True,
        blank=True,
        help_text="Longitude of the address, or null if it could not be geocoded.",
    )
    found = models.BooleanField(
        default=False,
        help_text="True if the geocoder returned a result for this address.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        if self.found and self.latitude is not None and self.longitude is not None:
            return f"{self.address} → ({self.latitude:.4f}, {self.longitude:.4f})"
        return f"{self.address} → not found"
