from django.core.management.base import BaseCommand

from programs.models import AddressGeocode, Student
from programs.utils import normalize_address, resolve_address_points


class Command(BaseCommand):
    help = (
        "Geocode student addresses ahead of time so the student map page loads "
        "instantly. Results are cached in the AddressGeocode model; only "
        "addresses not already cached are looked up."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--program",
            type=int,
            help="Only geocode addresses of students enrolled in this program.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many addresses would be geocoded without calling the service.",
        )
        parser.add_argument(
            "--retry-missing",
            action="store_true",
            help="Also retry addresses that were previously marked as not found.",
        )

    def handle(self, *args, **options):
        program_id = options.get("program")
        qs = Student.objects.all()
        if program_id:
            qs = qs.filter(enrollment__program_id=program_id)

        addresses = set()
        for student in qs.iterator():
            parts = [
                student.address,
                student.city,
                student.state,
                student.zip_code,
            ]
            addr = ", ".join(p for p in parts if p).strip(", ")
            if addr:
                addresses.add(addr)

        filter_args = {"address__in": [normalize_address(a) for a in addresses]}
        if not options.get("retry_missing"):
            # Only consider addresses we've never seen before (ignore "not found" ones)
            cached_keys = set(
                AddressGeocode.objects.filter(**filter_args).values_list(
                    "address", flat=True
                )
            )
        else:
            # Only consider addresses that were successfully geocoded (retry "not found" ones)
            cached_keys = set(
                AddressGeocode.objects.filter(found=True, **filter_args).values_list(
                    "address", flat=True
                )
            )
        uncached = [a for a in addresses if normalize_address(a) not in cached_keys]

        if options.get("dry_run"):
            self.stdout.write(
                f"DRY RUN: {len(uncached)} of {len(addresses)} unique "
                f"addresses would be geocoded."
            )
            return

        # If retrying, we need to clear the old "not found" entries so resolve_address_points
        # will actually hit the remote service again.
        if options.get("retry_missing") and not options.get("dry_run"):
            AddressGeocode.objects.filter(found=False, **filter_args).delete()

        points = resolve_address_points(addresses)
        found = sum(1 for point in points.values() if point)
        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {len(addresses)} unique addresses "
                f"({len(uncached)} newly geocoded, {found} resolved)."
            )
        )
