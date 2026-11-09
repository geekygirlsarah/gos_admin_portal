"""
find_disassociated_parents -- find converted applications whose students lost
their parent links.

Every converted student/guardian application (``applications/``) captured a
primary parent (step 7) and, when present, a secondary parent (step 8). When
the application was converted, those parents should have been linked to the
resulting ``Student`` (as ``primary_contact`` / ``secondary_contact`` via
``AdultStudentRelationship``). Old wizard bugs could create the parent's
``Adult`` record but fail to link it to the student, or link it so the
student's contact pointers point at the wrong person.

This command replays that expectation against the current data and reports,
per converted application:

* ``OK`` -- the expected parent is linked in the expected slot.
* ``ROLE_MISMATCH`` -- linked, but not as the primary/secondary contact the
  application designated.
* ``DISASSOCIATED`` -- exactly one ``Adult`` matches the application's parent
  (by name, or by email when no name was captured) but it is not linked to the
  student at all. Matching never relies on email alone, because parents in one
  family often share an address -- the parent is identified by name so a
  shared email doesn't point the app's primary at the secondary's record.
* ``AMBIGUOUS`` -- several adults match (e.g. duplicate same-name records)
  needs a human.
* ``MISSING`` -- no ``Adult`` record matches; the parent never made it in.
* ``NOT_CAPTURED`` -- the application has no usable data for that slot.
* ``MANUAL_REVIEW`` -- ownership or related-record links are inconsistent; no
  related identity is shown and no records are changed.

Read-only by default. Matching is scoped to the application's organization.
``--fix`` re-links only ownership-consistent ``DISASSOCIATED`` cases, explicitly
assigning that organization to new relationships. It never creates a new
``Adult``, changes foreign rows, or overrides an existing contact pointer. By
default only applications with at least one problem are shown; pass ``--all``
to list every converted application.

Usage::

    python manage.py find_disassociated_parents                # issues only
    python manage.py find_disassociated_parents --all          # every app
    python manage.py find_disassociated_parents --fix          # re-link orphans
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from applications.models import Application, normalize_step_keys
from programs.models import Adult, AdultStudentRelationship, Program, Student


def _email_key(value):
    return (value or "").strip().casefold()


def _name_key(adult_or_names):
    if isinstance(adult_or_names, Adult):
        first = (adult_or_names.legal_first_name or "").strip().casefold()
        last = (adult_or_names.last_name or "").strip().casefold()
        return first, last
    first, last = adult_or_names
    return (first or "").strip().casefold(), (last or "").strip().casefold()


def _adult_email_keys(adult):
    keys = set()
    for value in (adult.personal_email, adult.org_email):
        key = _email_key(value)
        if key:
            keys.add(key)
    return keys


def _descriptor(step):
    """Pull (email, legal_first_name, last_name) from a wizard step dict.

    Returns ``None`` when the step was skipped or holds no usable identity
    (no email and no first+last name).
    """
    if not step or step.get("_skipped"):
        return None
    email = (step.get("email") or "").strip().casefold() or None
    first = (step.get("legal_first_name") or step.get("first_name") or "").strip()
    last = (step.get("last_name") or "").strip()
    if not email and not (first and last):
        return None
    return {"email": email, "first": first, "last": last}


OK = "OK"
ROLE_MISMATCH = "ROLE_MISMATCH"
DISASSOCIATED = "DISASSOCIATED"
AMBIGUOUS = "AMBIGUOUS"
MISSING = "MISSING"
NOT_CAPTURED = "NOT_CAPTURED"
MANUAL_REVIEW = "MANUAL_REVIEW"

_ISSUE_STATUSES = (ROLE_MISMATCH, DISASSOCIATED, AMBIGUOUS, MISSING, MANUAL_REVIEW)
_MANUAL_REVIEW_DETAIL = (
    "Ownership or related-record inconsistency; manual review required. "
    "No linked identity was displayed or changed."
)


def _linked_name_match(linked_adults, desc):
    target = _name_key((desc["first"], desc["last"]))
    matches = [adult for adult in linked_adults if _name_key(adult) == target]
    if len(matches) == 1:
        return matches[0]
    return None


def _match_linked(linked_adults, desc):
    """Return the linked ``Adult`` that is the *same person* as the expected
    parent, or ``None``.

    Identity is decided by name, never by email alone: parents in one family
    often share an address, so an email-only match can land on the wrong family
    member (the app's primary can look "linked" when really the secondary's
    record happens to hold the shared email). When the application captured no
    usable name (email only), fall back to email identity.
    """
    match = _linked_name_match(linked_adults, desc)
    if match is not None:
        return match
    if not (desc["first"] and desc["last"]) and desc["email"]:
        for adult in linked_adults:
            if desc["email"] in _adult_email_keys(adult):
                return adult
    return None


def _adult_records_for_email(email, organization_id):
    matches = list(
        Adult._base_manager.filter(
            organization_id=organization_id, personal_email__iexact=email
        )
    )
    if not matches:
        matches = list(
            Adult._base_manager.filter(
                organization_id=organization_id, org_email__iexact=email
            )
        )
    return matches


def _adult_records_for_name(desc, organization_id):
    return list(
        Adult._base_manager.filter(
            organization_id=organization_id,
            legal_first_name__iexact=desc["first"],
            last_name__iexact=desc["last"],
        )
    )


def _foreign_adult_match_exists(desc, organization_id, *, by_email=False):
    adults = Adult._base_manager.exclude(organization_id=organization_id)
    if by_email:
        return adults.filter(
            Q(personal_email__iexact=desc["email"]) | Q(org_email__iexact=desc["email"])
        ).exists()
    return adults.filter(
        legal_first_name__iexact=desc["first"], last_name__iexact=desc["last"]
    ).exists()


def _name_label(desc):
    return f"{desc['first']} {desc['last']}".strip()


def _find_unlinked(student, desc, organization_id, linked_adults):
    """Look for an ``Adult`` that represents the expected parent but is not
    linked to the student.

    Returns ``(status, extra, detail)``. ``extra`` is the matched ``Adult`` or
    ``None``; ``detail`` is the human-readable explanation.
    """
    linked_ids = {adult.id for adult in linked_adults}

    if desc["first"] and desc["last"]:
        if _foreign_adult_match_exists(desc, organization_id):
            return MANUAL_REVIEW, None, _MANUAL_REVIEW_DETAIL
        name_matches = _adult_records_for_name(desc, organization_id)
        if len(name_matches) == 1:
            return (
                DISASSOCIATED,
                name_matches[0],
                f"Adult #{name_matches[0].pk} {name_matches[0].display_name} "
                "exists but is not linked to this student (matched by name)",
            )
        if len(name_matches) > 1:
            return (
                AMBIGUOUS,
                None,
                f"{len(name_matches)} adult records match this name -- needs "
                "manual review",
            )

    if desc["email"]:
        if _foreign_adult_match_exists(desc, organization_id, by_email=True):
            return MANUAL_REVIEW, None, _MANUAL_REVIEW_DETAIL
        email_matches = _adult_records_for_email(desc["email"], organization_id)
        if len(email_matches) > 1:
            return (
                AMBIGUOUS,
                None,
                f"{len(email_matches)} adult records share this email -- merge "
                "them first, then link",
            )
        if len(email_matches) == 1:
            adult = email_matches[0]
            if adult.id in linked_ids:
                return (
                    MISSING,
                    None,
                    f'no record exists for "{_name_label(desc)}"; Adult '
                    f"#{adult.pk} {adult.display_name} has this email but is "
                    "already linked -- likely a shared family email; the "
                    "application's parent has no record of their own",
                )
            if desc["first"] and desc["last"]:
                return (
                    MISSING,
                    None,
                    f'no record exists for "{_name_label(desc)}"; Adult '
                    f"#{adult.pk} {adult.display_name} has this email but a "
                    "different name -- likely a shared family email; review "
                    "manually",
                )
            return (
                DISASSOCIATED,
                adult,
                f"Adult #{adult.pk} {adult.display_name} exists but is not "
                "linked to this student",
            )

    return MISSING, None, "no adult record matches -- the parent never converted"


def classify(student, slot, desc, organization_id, linked_adults, relationships_by_id):
    """Compare one expected parent to the student's current links.

    Returns ``(status, extra, detail)``. ``extra`` is the matched ``Adult`` (or
    ``None``); ``detail`` is a short human-readable rendering for ``extra``.
    """
    match = _match_linked(linked_adults, desc)
    if match is not None:
        relation_id = getattr(student, f"{slot}_contact_relationship_id")
        contact_adult_id = (
            relationships_by_id[relation_id].adult_id if relation_id else None
        )
        if contact_adult_id == match.pk:
            return (
                OK,
                match,
                f"linked as {slot} -> Adult #{match.pk} {match.display_name}",
            )
        current = (
            "primary"
            if (
                student.primary_contact_relationship_id
                and relationships_by_id[
                    student.primary_contact_relationship_id
                ].adult_id
                == match.pk
            )
            else (
                "secondary"
                if (
                    student.secondary_contact_relationship_id
                    and relationships_by_id[
                        student.secondary_contact_relationship_id
                    ].adult_id
                    == match.pk
                )
                else "a linked parent"
            )
        )
        return (
            ROLE_MISMATCH,
            match,
            f"Adult #{match.pk} {match.display_name} is linked but not set "
            f"as the {slot} contact (currently {current})",
        )

    return _find_unlinked(student, desc, organization_id, linked_adults)


def _application_scope_is_consistent(application):
    organization_id = application.organization_id
    student = application.converted_student
    if organization_id is None or student is None:
        return False
    if student.organization_id != organization_id:
        return False
    return not application.program_id or (
        application.program is not None
        and application.program.organization_id == organization_id
    )


def _student_relationships(application):
    """Return local relationships by ID, or ``None`` for unsafe references."""
    student = application.converted_student
    organization_id = application.organization_id
    pointer_ids = {
        relation_id
        for relation_id in (
            student.primary_contact_relationship_id,
            student.secondary_contact_relationship_id,
        )
        if relation_id
    }
    relationships = list(
        AdultStudentRelationship._base_manager.filter(
            Q(student_id=student.pk) | Q(pk__in=pointer_ids)
        ).select_related("adult", "student")
    )
    relationships_by_id = {
        relationship.pk: relationship for relationship in relationships
    }
    for relationship in relationships:
        if (
            relationship.organization_id != organization_id
            or relationship.student_id != student.pk
            or relationship.student.organization_id != organization_id
            or relationship.adult.organization_id != organization_id
        ):
            return None
    if not pointer_ids.issubset(relationships_by_id):
        return None
    return relationships_by_id


def analyze_application(application):
    """Return a list of ``(slot, status, adult, desc, step, detail)`` rows for
    one converted application."""
    if not _application_scope_is_consistent(application):
        return [
            (
                "application",
                MANUAL_REVIEW,
                None,
                None,
                None,
                "Application, student, or program ownership is inconsistent; "
                "manual review required. No personal details were displayed or changed.",
            )
        ]

    relationships_by_id = _student_relationships(application)
    if relationships_by_id is None:
        return [
            (
                "application",
                MANUAL_REVIEW,
                None,
                None,
                None,
                _MANUAL_REVIEW_DETAIL,
            )
        ]
    student = application.converted_student
    linked_adults = [
        relationship.adult
        for relationship in relationships_by_id.values()
        if relationship.student_id == student.pk
    ]

    data = normalize_step_keys(application.data or {})
    rows = []
    for slot, key in (
        ("primary", "step7-primaryparent"),
        ("secondary", "step8-secondaryparent"),
    ):
        step = data.get(key)
        desc = _descriptor(step)
        if desc is None:
            rows.append(
                (slot, NOT_CAPTURED, None, None, step, "no parent data captured")
            )
            continue
        status, extra, detail = classify(
            student,
            slot,
            desc,
            application.organization_id,
            linked_adults,
            relationships_by_id,
        )
        rows.append((slot, status, extra, desc, step, detail))
    return rows


class Command(BaseCommand):
    help = (
        "Find converted applications whose students lost their parent links "
        "(read-only); --fix re-links only ownership-consistent local matches."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix",
            action="store_true",
            help=("Re-link only local, ownership-consistent DISASSOCIATED findings."),
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Include fully-OK applications in the report (issues only by default).",
        )

    def handle(self, *args, **options):
        fix = options["fix"]
        show_all = options["all"]

        applications = (
            Application._base_manager.filter(
                status=Application.Status.CONVERTED,
                applicant_type__in=(
                    Application.Type.STUDENT,
                    Application.Type.PARENT,
                ),
            )
            .exclude(converted_student__isnull=True)
            .select_related("converted_student", "program")
            .order_by("application_id")
        )

        rows_by_app = {}
        for application in applications:
            rows = analyze_application(application)
            rows_by_app[application.pk] = rows

        issue_apps = [
            application
            for application in applications
            if any(
                status in _ISSUE_STATUSES
                for _, status, *_ in rows_by_app[application.pk]
            )
        ]
        shown = applications if show_all else issue_apps

        self._print_report(shown, rows_by_app)

        if fix:
            linked = self._link_disassociated(issue_apps, rows_by_app)
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nRe-linked {linked} orphaned parent(s). "
                    "See audit log for GUARDIAN_ADDED entries."
                )
            )

        if not issue_apps:
            self.stdout.write(self.style.SUCCESS("\nNo disassociated parents found."))
        else:
            self.stdout.write(
                f"\n{len(issue_apps)} application(s) with issues. "
                "Review the report above and manually investigate any unsafe links."
            )

    # ------------------------------------------------------------------ link
    def _link_disassociated(self, applications, rows_by_app):
        from audit.events import AuditEvent
        from audit.service import log_event

        linked = 0
        for reported_application in applications:
            for slot, status, adult, desc, step, _ in rows_by_app[
                reported_application.pk
            ]:
                if status != DISASSOCIATED or adult is None:
                    continue
                linked_pair = self._link_one_disassociated(
                    reported_application.pk, slot, adult.pk
                )
                if linked_pair is None:
                    self.stdout.write(
                        self.style.WARNING(
                            "  Skipped a changed or inconsistent record; "
                            "manual review required."
                        )
                    )
                    continue
                application, student, adult, relationship, relationship_label = (
                    linked_pair
                )
                log_event(
                    request=None,
                    event=AuditEvent.GUARDIAN_ADDED,
                    resource=student,
                    after={
                        "guardian": str(adult),
                        "relationship": relationship_label,
                    },
                    notes=(
                        f"Parent re-linked to student by find_disassociated_parents "
                        f"--fix from application {application.application_id} "
                        f"(Adult pk={adult.pk})."
                    ),
                )
                linked += 1
                message = (
                    f"  linked {slot} parent Adult #{adult.pk} "
                    f"{adult.display_name} -> Student #{student.pk} "
                    f"{student.display_name}"
                )
                self.stdout.write(self.style.WARNING(message))
        return linked

    def _link_one_disassociated(self, application_id, slot, expected_adult_id):
        """Lock, recheck, and link one still-valid local parent recovery."""
        with transaction.atomic():
            application = (
                Application._base_manager.select_for_update()
                .filter(pk=application_id, status=Application.Status.CONVERTED)
                .first()
            )
            if application is None or application.converted_student_id is None:
                return None

            student = (
                Student._base_manager.select_for_update()
                .filter(pk=application.converted_student_id)
                .first()
            )
            if student is None:
                return None
            application.converted_student = student

            if application.program_id:
                program = (
                    Program._base_manager.select_for_update()
                    .filter(pk=application.program_id)
                    .first()
                )
                if program is None:
                    return None
                application.program = program

            if not _application_scope_is_consistent(application):
                return None

            adult = (
                Adult._base_manager.select_for_update()
                .filter(pk=expected_adult_id)
                .first()
            )
            if adult is None or adult.organization_id != application.organization_id:
                return None

            pointer_ids = {
                relation_id
                for relation_id in (
                    student.primary_contact_relationship_id,
                    student.secondary_contact_relationship_id,
                )
                if relation_id
            }
            list(
                AdultStudentRelationship._base_manager.select_for_update()
                .filter(Q(student_id=student.pk) | Q(pk__in=pointer_ids))
                .values_list("pk", flat=True)
            )

            matching_row = next(
                (row for row in analyze_application(application) if row[0] == slot),
                None,
            )
            if (
                matching_row is None
                or matching_row[1] != DISASSOCIATED
                or matching_row[2] is None
                or matching_row[2].pk != adult.pk
            ):
                return None

            step = matching_row[4] or {}
            relationship_label = (step.get("relationship_to_student") or "parent")[:20]
            specific = (step.get("specific_relationship") or "")[:100]
            relationship, _ = AdultStudentRelationship.objects.for_organization(
                application.organization
            ).get_or_create(
                organization_id=application.organization_id,
                adult=adult,
                student=student,
                defaults={
                    "relationship_to_student": relationship_label,
                    "specific_relationship": specific,
                },
            )

            if not adult.is_parent:
                adult.is_parent = True
                adult.save(update_fields=["is_parent"])

            if getattr(student, f"{slot}_contact_relationship_id") is None:
                setattr(student, f"{slot}_contact", adult)
                student.save()

            return application, student, adult, relationship, relationship_label

    # ---------------------------------------------------------------- report
    def _print_report(self, applications, rows_by_app):
        self.stdout.write(
            self.style.SUCCESS(
                "Disassociated parents report\n============================\n"
            )
        )
        if not applications:
            self.stdout.write("No converted applications to report.\n")
            return
        for application in applications:
            if _application_scope_is_consistent(application):
                student = application.converted_student
                program = application.program.name if application.program_id else "-"
                self.stdout.write(
                    f"Application {application.application_id} -- "
                    f"Student #{student.pk} {student.display_name} "
                    f"(program: {program})"
                )
            else:
                self.stdout.write(
                    f"Application {application.application_id} -- "
                    f"organization #{application.organization_id} "
                    "[MANUAL_REVIEW: ownership mismatch; personal details withheld]"
                )
            for slot, status, adult, desc, step, detail in rows_by_app[application.pk]:
                if status == MANUAL_REVIEW:
                    name = "identity withheld"
                    email = ""
                    role = ""
                elif desc is not None:
                    name = f"{desc['first']} {desc['last']}".strip() or "no name"
                    email = f" <{desc['email']}>" if desc["email"] else ""
                    role = self._role_label(step)
                else:
                    name = "no name"
                    email = ""
                    role = self._role_label(step)
                self.stdout.write(
                    f'  {slot.title()}: "{name}"{email}  [{status}]{role}'
                )
                if detail:
                    self.stdout.write(f"      {detail}")
            self.stdout.write("")

    def _role_label(self, step):
        """Render the captured ``relationship_to_student`` (e.g. parent) and
        the free-text ``specific_relationship`` (e.g. grandfather), e.g.
        ``  (relationship: parent/grandfather)``. Empty when the application
        captured neither."""
        if not isinstance(step, dict):
            return ""
        relationship = (step.get("relationship_to_student") or "").strip()
        specific = (step.get("specific_relationship") or "").strip()
        if not relationship and not specific:
            return ""
        label = relationship or specific
        if relationship and specific:
            label = f"{relationship}/{specific}"
        return f"  (relationship: {label})"
