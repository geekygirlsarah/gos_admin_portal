"""Management command to anonymize all email addresses across the database for local development and testing safety."""

from __future__ import annotations

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from applications.models import Application
from programs.models import Adult, Student

try:
    from allauth.account.models import EmailAddress
except ImportError:
    EmailAddress = None


class Command(BaseCommand):
    help = (
        "Safely anonymize all email addresses across Students, Adults, Users, and "
        "Applications to prevent accidental outgoing emails during local development "
        "with real imported databases."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--domain",
            type=str,
            default="example.com",
            help="Domain to use for anonymized email addresses (default: example.com).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate the anonymization and display counts without saving to DB.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Confirm and apply the changes to the database.",
        )

    def handle(self, *args, **options):
        domain = options["domain"].lstrip("@").strip()
        dry_run = options["dry_run"]
        force = options["force"]

        if not dry_run and not force:
            self.stdout.write(
                self.style.WARNING(
                    "Safety check: You must specify --force to modify the database, "
                    "or --dry-run to preview changes."
                )
            )
            return

        prefix = "[DRY-RUN] " if dry_run else ""
        self.stdout.write(
            f"{prefix}Anonymizing email addresses with domain '@{domain}'..."
        )

        student_count = 0
        adult_count = 0
        user_count = 0
        app_count = 0

        with transaction.atomic():
            # 1. Students
            for student in Student.objects.all():
                updated = False
                if student.personal_email:
                    new_personal = f"student_{student.pk}@{domain}"
                    if student.personal_email != new_personal:
                        student.personal_email = new_personal
                        updated = True
                if student.andrew_email:
                    new_andrew = f"student_{student.pk}_andrew@{domain}"
                    if student.andrew_email != new_andrew:
                        student.andrew_email = new_andrew
                        updated = True
                if updated:
                    student_count += 1
                    if not dry_run:
                        student.save(update_fields=["personal_email", "andrew_email"])

            # 2. Adults
            for adult in Adult.objects.all():
                updated = False
                if adult.personal_email:
                    new_personal = f"adult_{adult.pk}@{domain}"
                    if adult.personal_email != new_personal:
                        adult.personal_email = new_personal
                        updated = True
                if adult.andrew_email:
                    new_andrew = f"adult_{adult.pk}_andrew@{domain}"
                    if adult.andrew_email != new_andrew:
                        adult.andrew_email = new_andrew
                        updated = True
                if updated:
                    adult_count += 1
                    if not dry_run:
                        adult.save(update_fields=["personal_email", "andrew_email"])

            # 3. Users & EmailAddress
            for user in User.objects.all():
                # Derive email matching linked Student/Adult if possible
                new_email = None
                if (
                    hasattr(user, "student_profile")
                    and user.student_profile
                    and user.student_profile.personal_email
                ):
                    new_email = user.student_profile.personal_email
                elif (
                    hasattr(user, "adult_profile")
                    and user.adult_profile
                    and user.adult_profile.personal_email
                ):
                    new_email = user.adult_profile.personal_email
                elif user.email:
                    new_email = f"user_{user.pk}@{domain}"

                if new_email and user.email != new_email:
                    user_count += 1
                    if not dry_run:
                        old_email = user.email
                        user.email = new_email
                        user.save(update_fields=["email"])
                        if EmailAddress:
                            EmailAddress.objects.filter(user=user).update(
                                email=new_email
                            )

            # 4. Applications
            for app in Application.objects.all():
                new_app_email = f"application_{app.pk}@{domain}"
                if app.email != new_app_email:
                    app_count += 1
                    if not dry_run:
                        app.email = new_app_email
                        if app.data and isinstance(app.data, dict):
                            # Scrub emails from step dictionaries
                            for step_k, step_v in app.data.items():
                                if isinstance(step_v, dict):
                                    for field_k, field_v in step_v.items():
                                        if (
                                            "email" in field_k.lower()
                                            and isinstance(field_v, str)
                                            and "@" in field_v
                                        ):
                                            step_v[field_k] = (
                                                f"applicant_{app.pk}_{field_k}@{domain}"
                                            )
                        app.save(update_fields=["email", "data"])

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}Completed: "
                f"{student_count} student(s), {adult_count} adult(s), "
                f"{user_count} user(s), {app_count} application(s) anonymized."
            )
        )
