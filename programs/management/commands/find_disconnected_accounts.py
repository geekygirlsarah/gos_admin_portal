"""
find_disconnected_accounts -- surface accounts disconnected from their profiles.

A portal bug silently NULL-ed ``Student.user`` (and could affect ``Adult.user``)
when a privileged user edited a profile, leaving the person unable to sign in.
This command finds the evidence:

* floating User accounts that are no longer linked to any Student or Adult
  profile (the accounts that lost their link),
* unlinked Student/Adult profiles that had a login they no longer have, and
* unambiguous email/name matches between the two (strongly-suspected victims).

It is read-only by default. ``--fix`` fills the NULL ``user`` link only for
unambiguous matches, and only when the link is currently empty — it never
overwrites an existing link or re-points a user that already belongs to another
profile.

Usage::

    python manage.py find_disconnected_accounts          # report only
    python manage.py find_disconnected_accounts --fix    # re-link unambiguous matches
"""

from __future__ import annotations

from collections import defaultdict

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from programs.models import Adult, Student

User = get_user_model()


def _profile_name_key(profile):
    first = profile.preferred_first_name or profile.legal_first_name
    return (first.casefold(), profile.last_name.casefold())


def _profile_emails(profile):
    emails = []
    if getattr(profile, "personal_email", None):
        emails.append(profile.personal_email.casefold())
    return emails


def _find_pairs(fixable_floating, candidates):
    """Pair floating users with unlinked profiles (email, then name).

    Only profiles that match each other exactly once are considered
    unambiguous; a floating user that matches two different profiles makes
    both pairs ambiguous.
    """
    by_email = {}
    by_name = {}
    for user in fixable_floating:
        if user.email:
            by_email.setdefault(user.email.casefold(), []).append(user)
        if user.first_name and user.last_name:
            by_name.setdefault(
                (user.first_name.casefold(), user.last_name.casefold()), []
            ).append(user)

    pairs = [  # (label, profile, user, method)
        (label, profile, *_match_one(profile, name_key, emails, by_email, by_name))
        for label, profile, name_key, emails in candidates
    ]
    pairs = [p for p in pairs if p[2] is not None]

    counts = defaultdict(int)
    for _, _, user, _ in pairs:
        counts[user.pk] += 1
    unambiguous = [p for p in pairs if counts[p[2].pk] == 1]
    ambiguous = [p for p in pairs if counts[p[2].pk] > 1]
    return unambiguous, ambiguous


def _match_one(profile, name_key, emails, by_email, by_name):
    """Return the (user, method) a single profile matches, or (None, None)."""
    for email in emails:
        pool = by_email.get(email, [])
        if len(pool) == 1:
            return pool[0], "email"
    pool = by_name.get(name_key, [])
    if len(pool) == 1:
        return pool[0], "name"
    return None, None


class Command(BaseCommand):
    help = (
        "Find User accounts disconnected from their Student/Adult profiles "
        "(read-only), and optionally re-link unambiguous matches with --fix."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix",
            action="store_true",
            help="Re-link unambiguous matches (empty links only).",
        )

    def handle(self, *args, **options):
        fix = options["fix"]
        floating, unlinked_students, unlinked_adults = self._collect()
        fixable_floating = [u for u in floating if not (u.is_staff or u.is_superuser)]
        candidates = self._candidates(unlinked_students, unlinked_adults)
        unambiguous, ambiguous = _find_pairs(fixable_floating, candidates)

        self._print_report(
            floating,
            unlinked_students,
            unlinked_adults,
            unambiguous,
            ambiguous,
            fix,
        )

        if fix:
            self._apply_links(unambiguous)

        if not floating and not unambiguous and not ambiguous:
            summary = self.style.SUCCESS("No disconnected accounts found.")
        else:
            summary = "Review the report above before taking action."
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(summary)

    # ------------------------------------------------------------------ data
    def _collect(self):
        """Return floating users, then unlinked Students/Adults."""
        floating = list(
            User.objects.filter(
                student_profile__isnull=True,
                adult_profile__isnull=True,
            ).order_by("date_joined")
        )
        unlinked_students = list(Student.objects.filter(user__isnull=True))
        unlinked_adults = list(Adult.objects.filter(user__isnull=True))
        return floating, unlinked_students, unlinked_adults

    def _candidates(self, unlinked_students, unlinked_adults):
        candidates = [
            ("Student", s, _profile_name_key(s), _profile_emails(s))
            for s in unlinked_students
        ]
        candidates += [
            ("Adult", a, _profile_name_key(a), _profile_emails(a))
            for a in unlinked_adults
        ]
        return candidates

    # ---------------------------------------------------------------- report
    def _print_report(
        self,
        floating,
        unlinked_students,
        unlinked_adults,
        unambiguous,
        ambiguous,
        fix,
    ):
        self.stdout.write(
            self.style.SUCCESS("Disconnected accounts report\n===================\n")
        )

        self.stdout.write(f"[1] FLOATING USER ACCOUNTS ({len(floating)})\n{'-' * 60}")
        if floating:
            for user in floating:
                marker = (
                    " (staff/superuser)" if user.is_staff or user.is_superuser else ""
                )
                self.stdout.write(
                    f"  #{user.pk} {user.email or user.username} — created "
                    f"{user.date_joined:%Y-%m-%d}{marker}"
                )
        else:
            self.stdout.write("  None.\n")

        self.stdout.write(
            f"\n[2] UNLINKED PROFILES WITHOUT A MATCHING ACCOUNT\n{'-' * 60}"
        )
        matched_ids = {p.pk for _, p, _, _ in unambiguous + ambiguous}
        leftover_students = [s for s in unlinked_students if s.pk not in matched_ids]
        leftover_adults = [a for a in unlinked_adults if a.pk not in matched_ids]
        if leftover_students or leftover_adults:
            if leftover_students:
                self.stdout.write(f"  Students: {len(leftover_students)}")
            if leftover_adults:
                self.stdout.write(f"  Adults: {len(leftover_adults)}")
            self.stdout.write(
                "  (No floating account matched these profiles; they may "
                "never have had a login.)"
            )
        else:
            self.stdout.write("  None.\n")

        self.stdout.write(f"\n[3] CANDIDATE RE-LINKS\n{'-' * 60}")
        if unambiguous:
            verb = "would link" if not fix else "linked"
            for label, profile, user, method in unambiguous:
                self.stdout.write(
                    self.style.WARNING(
                        f"  {verb}: {label} #{profile.pk} ({profile.display_name}) "
                        f"<-> # {user.pk} {user.email or user.username} via {method}"
                    )
                )
        else:
            self.stdout.write("  None.\n")

        if ambiguous:
            self.stdout.write(
                "\n  AMBIGUOUS — skipped (account matches multiple profiles):"
            )
            for label, profile, user, method in ambiguous:
                self.stdout.write(
                    f"    {label} #{profile.pk} ({profile.display_name}) "
                    f"<-> {user.email or user.username} via {method}"
                )

    # ----------------------------------------------------------------- apply
    def _apply_links(self, unambiguous):
        """Fill NULL links for unambiguous matches. Never overwrites or steals."""
        linked = 0
        for label, profile, user, method in unambiguous:
            if (
                Student.objects.filter(user=user).exists()
                or Adult.objects.filter(user=user).exists()
            ):
                continue
            profile.user = user
            profile.save(update_fields=["user"])
            linked += 1
        if linked:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nLinked {linked} profile(s). "
                    "See audit log for PROFILE_LINK_CHANGED entries."
                )
            )
        else:
            self.stdout.write("\nNothing to link.")
