from django.core.management.base import BaseCommand, CommandError

from applications.answers import EXPERIENCE_QUESTIONS
from applications.models import Application
from organizations.models import Organization


class Command(BaseCommand):
    help = "Report approved student applications whose answers need manual review."

    def add_arguments(self, parser):
        parser.add_argument("--organization", required=True, help="Organization slug")

    def handle(self, *args, **options):
        organization = Organization.objects.filter(slug=options["organization"]).first()
        if organization is None:
            raise CommandError("Organization not found.")
        applications = (
            Application.objects.for_organization(organization)
            .filter(
                status__in=(
                    Application.Status.APPROVED,
                    Application.Status.APPROVED_SIGNED,
                    Application.Status.CONVERTED,
                )
            )
            .exclude(applicant_type=Application.Type.MENTOR)
            .select_related("converted_student", "program")
            .order_by("pk")
        )
        count = 0
        for application in applications.iterator(chunk_size=500):
            if (
                application.program_id
                and application.program.organization_id != organization.pk
            ) or (
                application.converted_student_id
                and application.converted_student.organization_id != organization.pk
            ):
                reason = "inconsistent ownership"
            elif not application.converted_student_id:
                reason = "no linked student"
            else:
                data = application.data
                if not isinstance(data, dict):
                    reason = "malformed answer data"
                else:
                    malformed = False
                    for step in ("step6-experience", "step6"):
                        values = data.get(step)
                        if values is None:
                            continue
                        if not isinstance(values, dict) or any(
                            value is not None and not isinstance(value, str)
                            for key, value in values.items()
                            if key in EXPERIENCE_QUESTIONS
                        ):
                            malformed = True
                    if not malformed:
                        continue
                    reason = "malformed answer data"
            self.stdout.write(f"{application.application_id}: {reason}")
            count += 1
        self.stdout.write(f"Applications requiring review: {count}")
