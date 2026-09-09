from django.core.management.base import BaseCommand, CommandError

from programs.models import Adult, Enrollment, Program, Student


class Command(BaseCommand):
    help = (
        "Collect email addresses for a program's recipients so they can be "
        "pasted into a BCC field. Mirrors the recipient logic used by the "
        "ProgramEmailView."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--program-id",
            type=int,
            required=True,
            help="Primary key of the program to collect emails for.",
        )
        parser.add_argument(
            "--students",
            action="store_true",
            help="Include active, non-graduated students enrolled in the program.",
        )
        parser.add_argument(
            "--parents",
            action="store_true",
            help=(
                "Include parents/guardians of active students in the program "
                "who have opted in to email updates."
            ),
        )
        parser.add_argument(
            "--mentors",
            action="store_true",
            help="Include all active mentors (not scoped to the program).",
        )
        parser.add_argument(
            "--team-id",
            type=int,
            action="append",
            dest="team_ids",
            help="Optionally filter to students on specific team IDs (can be repeated).",
        )
        parser.add_argument(
            "--crew-id",
            type=int,
            action="append",
            dest="crew_ids",
            help="Optionally filter to students on specific crew IDs (can be repeated).",
        )
        parser.add_argument(
            "--subteam-id",
            type=int,
            action="append",
            dest="subteam_ids",
            help="Optionally filter to students on specific subteam IDs (can be repeated).",
        )

    def handle(self, *args, **options):
        program_id = options["program_id"]
        try:
            prog = Program.objects.get(pk=program_id)
        except Program.DoesNotExist:
            raise CommandError(f"Program with id {program_id} does not exist.")

        groups = []
        if options["students"]:
            groups.append("students")
        if options["parents"]:
            groups.append("parents")
        if options["mentors"]:
            groups.append("mentors")

        if not groups:
            self.stdout.write(
                "No recipient groups specified. Use --students, --parents, or --mentors."
            )
            return

        enrollments = Enrollment.objects.filter(
            program=prog,
            active=True,
            student__graduated=False,
        )
        if options.get("team_ids"):
            enrollments = enrollments.filter(team_id__in=options["team_ids"])
        if options.get("crew_ids"):
            enrollments = enrollments.filter(crew_id__in=options["crew_ids"])
        if options.get("subteam_ids"):
            enrollments = enrollments.filter(subteam_id__in=options["subteam_ids"])

        filtered_student_ids = enrollments.values_list("student_id", flat=True)

        recipients = set()

        if "students" in groups:
            for s in Student.objects.filter(id__in=filtered_student_ids).distinct():
                if s.personal_email:
                    recipients.add(s.personal_email)
                elif s.andrew_email:
                    recipients.add(s.andrew_email)

        if "parents" in groups:
            for parent in Adult.objects.filter(
                students__id__in=filtered_student_ids,
                email_updates=True,
                login_enabled=True,
            ).distinct():
                e = parent.personal_email or parent.andrew_email
                if e:
                    recipients.add(e)

        if "mentors" in groups:
            for m in Adult.objects.filter(is_mentor=True, mentor_active=True):
                e = m.personal_email or m.andrew_email
                if e:
                    recipients.add(e)

        if not recipients:
            self.stdout.write(
                f"No email addresses found for {prog.name} with the selected groups."
            )
            return

        self.stdout.write(", ".join(sorted(recipients)))
