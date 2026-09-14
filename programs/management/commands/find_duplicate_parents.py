"""
find_duplicate_parents -- surface duplicate parent/guardian Adult records.

Old application-wizard bugs could create more than one ``Adult`` row for the
same parent while converting an application: matching by email failed on a
name variant and a brand-new record was created, or a parent with no captured
name was stored with the placeholders ``(unknown)``/``(unknown)``.

This command only looks at ``Adult`` rows with ``is_parent=True`` and reports
two kinds of groups:

* *strong* duplicates -- the same email plus the same full name, or (when
  there is no email) the same name plus the same phone number. These are
  almost certainly the same person and are eligible for automatic merging.
* *review* candidates -- parents sharing an email where one record has no
  usable name, or where two names are suspiciously similar (a first-name
  variant). These may be the same person, but a human should look first.

Read-only by default. ``--fix`` merges only the strong groups into a single
surviving record, reusing the exact merge logic from the portal's
"Merge Parents" page; review candidates are always left for a human. A group
is never auto-merged when more than one of its members is linked to its own
login account (those are probably two different people).

Usage::

    python manage.py find_duplicate_parents           # report only
    python manage.py find_duplicate_parents --fix      # merge unambiguous groups
"""

from __future__ import annotations

import re
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from programs.models import Adult

_PLACEHOLDER = "(unknown)"


def _email_key(value):
    return (value or "").strip().casefold()


def _phone_key(value):
    return re.sub(r"\D", "", value or "")


def _name_key(adult):
    first = (adult.legal_first_name or "").strip().casefold()
    last = (adult.last_name or "").strip().casefold()
    return first, last


def _is_placeholder_name(adult):
    first, last = _name_key(adult)
    return not first or not last or _PLACEHOLDER in (first, last)


def _completeness_score(adult):
    """A rough *how much real data is on this record* score for choosing the
    survivor of a merge. Ties favour the record created first."""
    score = 0
    if _email_key(adult.personal_email):
        score += 3
    if _email_key(adult.andrew_email):
        score += 1
    if _phone_key(adult.phone_number):
        score += 2
    for field in (
        "address",
        "city",
        "state",
        "preferred_first_name",
        "emergency_contact_name",
        "emergency_contact_phone",
        "discord_username",
        "notes",
    ):
        if (getattr(adult, field) or "").strip():
            score += 1
    for field in (
        "on_discord",
        "has_cmu_id_card",
        "has_cmu_building_access",
        "has_google_team_drive_access",
        "has_google_mentor_drive_access",
        "has_google_admin_drive_access",
        "on_first_website",
        "signed_first_consent_form",
        "on_canvas",
        "has_zoom_account",
        "in_onshape_classroom",
        "on_canva",
        "on_google_mentor_group",
        "on_google_field_crew_group",
        "email_updates",
    ):
        if getattr(adult, field):
            score += 1
    if adult.user_id:
        score += 3
    score += adult.students.count() * 2
    return score


def _pick_keep(adults):
    return max(adults, key=lambda a: (_completeness_score(a), -a.pk))


def _strong_rows_by_name(adults, kind, label):
    """Strong groups: two or more named adults sharing the exact same name."""
    groups = []
    by_name = defaultdict(list)
    for adult in adults:
        by_name[_name_key(adult)].append(adult)
    for name, members in sorted(by_name.items()):
        if len(members) >= 2:
            groups.append(
                {
                    "kind": kind,
                    "label": label,
                    "adults": sorted(members, key=lambda a: a.pk),
                }
            )
    return groups


def _similar_name_reviews(real, email):
    """Review candidates: named adults sharing an email with variant first
    names (e.g. one is a prefix of the other)."""
    reviews = []
    real_sorted = sorted(real, key=lambda a: _name_key(a))
    for i in range(len(real_sorted)):
        for j in range(i + 1, len(real_sorted)):
            first_i, _ = _name_key(real_sorted[i])
            first_j, _ = _name_key(real_sorted[j])
            if _prefix_variants(first_i, first_j):
                reviews.append(
                    {
                        "kind": "email-similar-name",
                        "label": email,
                        "adults": [real_sorted[i], real_sorted[j]],
                        "note": (
                            f"Adults share email {email} and similar first "
                            "names -- may be the same person (or two people "
                            "sharing an email); review and merge manually."
                        ),
                    }
                )
    return reviews


def _prefix_variants(first_i, first_j):
    return bool(
        first_i
        and first_j
        and first_i != first_j
        and (first_j.startswith(first_i) or first_i.startswith(first_j))
    )


def _anonymous_review(adults_by_email, email):
    real = [a for a in adults_by_email if not _is_placeholder_name(a)]
    anon = [a for a in adults_by_email if _is_placeholder_name(a)]
    if not anon:
        return real, None
    if real:
        note = (
            f"{len(anon)} parent record(s) with no usable name share "
            f"email {email} with a named parent -- likely conversion "
            "artifacts of the same person; review and merge manually."
        )
    else:
        note = (
            f"{len(anon)} unnamed parent record(s) all share email "
            f"{email} with each other -- likely conversion artifacts "
            "of the same person; review and merge manually."
        )
    review = {
        "kind": "email-anonymous",
        "label": email,
        "adults": sorted(real + anon, key=lambda a: a.pk),
        "note": note,
    }
    return real, review


def _email_duplicates(by_email):
    strong = []
    review = []
    for email, adults in sorted(by_email.items()):
        if len(adults) < 2:
            continue
        real, anon_review = _anonymous_review(adults, email)
        if anon_review:
            review.append(anon_review)
        strong.extend(_strong_rows_by_name(real, "email-name", email))
        review.extend(_similar_name_reviews(real, email))
    return strong, review


def _phone_duplicates(no_email):
    by_phone = defaultdict(list)
    for adult in no_email:
        key = _phone_key(adult.phone_number)
        if key:
            by_phone[key].append(adult)
    strong = []
    for phone, adults in sorted(by_phone.items()):
        if len(adults) < 2:
            continue
        real = [a for a in adults if not _is_placeholder_name(a)]
        strong.extend(_strong_rows_by_name(real, "phone-name", phone))
    return strong


def find_duplicate_groups(parents):
    """Group duplicate-looking parents into (strong, review) group dicts.

    Each strong group dict has ``kind``, ``label`` and ``adults``; review
    groups additionally carry a ``note`` explaining why they were flagged.
    """
    by_email = defaultdict(list)
    no_email = []
    for adult in parents:
        key = _email_key(adult.personal_email)
        if key:
            by_email[key].append(adult)
        else:
            no_email.append(adult)

    strong, review = _email_duplicates(by_email)
    strong.extend(_phone_duplicates(no_email))
    return strong, review


def _group_has_multiple_logins(group):
    return sum(1 for a in group["adults"] if a.user_id) >= 2


class Command(BaseCommand):
    help = (
        "Find duplicate parent/guardian Adult records (read-only); "
        "--fix merges only unambiguous duplicates."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix",
            action="store_true",
            help=(
                "Merge unambiguous duplicate groups (same email + name, or "
                "same name + phone). Review candidates are never touched."
            ),
        )

    def handle(self, *args, **options):
        fix = options["fix"]
        parents = list(
            Adult.objects.filter(is_parent=True)
            .order_by("pk")
            .prefetch_related("students")
        )
        strong, review = find_duplicate_groups(parents)

        merged_info = {}
        skipped = []
        if fix:
            skipped, merged_info = self._merge_strong_groups(strong)

        self._print_report(strong, review, merged_info, skipped, fix)

    # ------------------------------------------------------------------ merge
    def _merge_strong_groups(self, strong):
        """Merge unambiguous groups; return (skipped groups, merged info).

        ``merged_info`` maps ``id(group)`` -> ``(keep, [(source_pk, ...)])``
        using values captured *before* the source rows were deleted (a deleted
        model instance has its ``pk`` stripped).
        """
        from audit.events import AuditEvent
        from audit.service import log_event
        from programs.utils import transfer_user_account
        from programs.views.adults import (
            _carry_over_missing_parent_fields,
            _merge_parent_role_flags,
            _transfer_parent_related_records,
            _transfer_parent_relationships,
        )

        skipped = []
        merged_info = {}
        for group in strong:
            if _group_has_multiple_logins(group):
                skipped.append(group)
                continue
            keep = _pick_keep(group["adults"])
            sources = [a for a in group["adults"] if a.pk != keep.pk]
            source_snapshots = []
            for source in sources:
                source_pk = source.pk
                source_name = source.display_name
                source_snapshots.append((source_pk, source_name))
                with transaction.atomic():
                    _transfer_parent_relationships(keep, source)
                    _transfer_parent_related_records(keep, source)
                    changed = _carry_over_missing_parent_fields(keep, source)
                    changed = _merge_parent_role_flags(keep, source) or changed
                    changed = transfer_user_account(keep, source) or changed
                    if changed:
                        keep.save()
                    source.delete()
                log_event(
                    request=None,
                    event=AuditEvent.RECORDS_MERGED,
                    resource=keep,
                    notes=(
                        f'Duplicate parent "{source_name}" (pk={source_pk}) '
                        f'merged into "{keep.display_name}" (pk={keep.pk}) by '
                        "find_duplicate_parents --fix. Student relationships "
                        "were transferred."
                    ),
                )
            merged_info[id(group)] = (keep, source_snapshots)
        return skipped, merged_info

    # ---------------------------------------------------------------- report
    def _print_report(self, strong, review, merged_info, skipped, fix):
        self.stdout.write(
            self.style.SUCCESS("Duplicate parents report\n========================\n")
        )
        if not strong and not review:
            self.stdout.write(self.style.SUCCESS("No duplicate parents found."))
            return

        self._print_strong(strong, merged_info, skipped, fix)
        self._print_review(review)

    def _print_strong(self, strong, merged_info, skipped, fix):
        self.stdout.write(
            f"[1] UNAMBIGUOUS DUPLICATES ({len(strong)} group(s))\n{'-' * 60}"
        )
        if not strong:
            self.stdout.write("  None.\n")
            return
        for group in strong:
            kind_label = {
                "email-name": f'Uses email {group["label"]}',
                "phone-name": f'No email; same name + phone {group["label"]}',
            }[group["kind"]]
            self.stdout.write(
                f'  Group: {kind_label}  ({len(group["adults"])} records)'
            )
            if not fix:
                keep = _pick_keep(group["adults"])
                for adult in group["adults"]:
                    tag = "survivor" if adult.pk == keep.pk else "duplicate"
                    self.stdout.write(f"    {tag:8s}: {self._fmt_adult(adult, tag)}")
                self.stdout.write(
                    f"    Suggested: merge the duplicate(s) into #{keep.pk} "
                    "(most complete record), e.g. via the Merge Parents page."
                )
            elif id(group) in merged_info:
                keep, source_snapshots = merged_info[id(group)]
                self.stdout.write(f"    keep  : {self._fmt_adult(keep, 'survivor')}")
                for source_pk, source_name in source_snapshots:
                    self.stdout.write(
                        f"    merged: Adult #{source_pk} {source_name} " f"[into keep]"
                    )
            elif group in skipped:
                self.stdout.write(
                    "    SKIPPED: multiple records have their own login "
                    "account -- likely different people; merge manually."
                )
            self.stdout.write("")

    def _print_review(self, review):
        self.stdout.write(
            f"[2] NEEDS MANUAL REVIEW ({len(review)} candidate(s))\n{'-' * 60}"
        )
        if not review:
            self.stdout.write("  None.\n")
            return
        for group in review:
            self.stdout.write(f'  - {group["note"]}')
            for adult in group["adults"]:
                self.stdout.write(f"      {self._fmt_adult(adult, 'review')}")
            self.stdout.write("")

    def _fmt_adult(self, adult, tag):
        email = _email_key(adult.personal_email) or "-"
        phone = _phone_key(adult.phone_number) or "-"
        students = adult.students.count()
        login = adult.user.email if adult.user_id else "-"
        return (
            f"Adult #{adult.pk} {adult.display_name} "
            f"[{tag}] email:{email} phone:{phone} "
            f"students:{students} login:{login}"
        )
