"""Backfill UserOrganizationMembership rows for existing Adult/Student profiles.

The post_save sync keeps profiles created after deployment in sync
automatically; this command closes the gap for profiles that already exist
without membership rows (created between organizations migration 0003 and the
sync signal). It is idempotent: profiles whose memberships already exist are
left untouched, and existing membership rows (including ones deactivated by a
platform admin) are never modified.
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Create missing UserOrganizationMembership rows for Adult and Student "
        "profiles (idempotent gap backfill)."
    )

    def handle(self, *args, **options):
        from programs.models import Adult, Student
        from programs.signals import sync_adult_memberships, sync_student_memberships

        created = 0
        for adult in Adult.objects.filter(user__isnull=False).iterator():
            created += sync_adult_memberships(adult)
        for student in Student.objects.filter(user__isnull=False).iterator():
            created += sync_student_memberships(student)

        self.stdout.write(
            self.style.SUCCESS(f"Created {created} organization membership(s).")
        )
