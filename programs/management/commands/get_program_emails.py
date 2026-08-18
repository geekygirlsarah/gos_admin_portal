from django.core.management.base import BaseCommand, CommandError

from programs.models import Adult, Program, Student


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

        recipients = set()

        if "students" in groups:
            for s in Student.objects.filter(
                enrollment__program=prog,
                enrollment__active=True,
                graduated=False,
            ).distinct():
                if s.personal_email:
                    recipients.add(s.personal_email)
                elif s.andrew_email:
                    recipients.add(s.andrew_email)

        if "parents" in groups:
            for parent in Adult.objects.filter(
                students__enrollment__program=prog,
                students__enrollment__active=True,
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
