"""Program-scoped student mail merge workflow."""

import json
import logging
import re

from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db.models import Q, Value
from django.db.models.functions import Coalesce, Lower, NullIf
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from attendance.models import AttendanceSession
from attendance.stats import get_attendance_stats_by_student
from organizations.models import Organization
from outreach.utils import get_outreach_stats_by_student
from programs.forms import ProgramEmailForm
from programs.models import (
    AdultStudentRelationship,
    Crew,
    Enrollment,
    Program,
    RolePermission,
    SubTeam,
    Team,
)
from programs.permission_views import (
    can_user_mail_merge,
    can_user_read,
    get_user_role,
)
from programs.utils.email_async import send_messages_in_background
from programs.utils.email_html import build_email_parts
from programs.utils.mail_merge import (
    BUILT_IN_FIELDS,
    MAX_MERGE_ROWS,
    analyze_merge_fields,
    parse_merge_data,
    substitute_html_fields,
    substitute_subject,
    substitute_text_fields,
)
from programs.utils.notifications import _mark_delivery_tracking, get_sender_connection

logger = logging.getLogger(__name__)

BASE_FIELD_LABELS = {
    "first_name": "First name",
    "last_name": "Last name",
    "full_name": "Full name",
    "email": "Student email",
    "program_name": "Program",
    "team_name": "Team",
    "crew_name": "Crew",
    "project_name": "Project (crew)",
    "subteam_name": "Subteam",
}
GRID_HIDDEN_FIELDS = {"email", "program_name"}
ATTENDANCE_FIELD_LABELS = {
    "attendance_total_hours": "Attendance total hours",
    "attendance_average_hours_per_week": "Attendance average hours per week",
    "attendance_sessions_count": "Attendance sessions attended",
    "attendance_days_attended": "Attendance days attended",
    "attendance_last_attended": "Attendance last attended",
}
OUTREACH_FIELD_LABELS = {
    "outreach_total_hours": "Outreach completed hours",
    "outreach_pending_hours": "Outreach pending hours",
    "outreach_shifts_attended": "Outreach shifts attended",
    "outreach_shifts_signed_up": "Outreach shifts signed up",
    "outreach_events_championed": "Outreach events championed",
    "outreach_shifts_unconfirmed": "Outreach past shifts unconfirmed",
}
# Grid column groups that can be shown or hidden above the mail merge table.
# First and last name stay visible (and sticky) so rows remain identifiable.
GRID_COLUMN_GROUPS = (
    ("details", "Full name"),
    ("groups", "Team, crew & subteam"),
    ("attendance", "Attendance"),
    ("outreach", "Outreach"),
)
STICKY_GRID_FIELDS = ("first_name", "last_name")


def _grid_column_group(field):
    if field in STICKY_GRID_FIELDS:
        return ""
    if field in ATTENDANCE_FIELD_LABELS:
        return "attendance"
    if field in OUTREACH_FIELD_LABELS:
        return "outreach"
    if field in ("team_name", "crew_name", "project_name", "subteam_name"):
        return "groups"
    return "details"


def _get_selected_ids(query_data, key, allowed_queryset):
    raw_ids = query_data.getlist(key)
    if len(raw_ids) > MAX_MERGE_ROWS:
        raise Http404
    try:
        ids = [int(value) for value in raw_ids]
    except (TypeError, ValueError):
        raise Http404
    if len(ids) != len(set(ids)):
        raise Http404
    allowed_ids = set(allowed_queryset.filter(pk__in=ids).values_list("pk", flat=True))
    if allowed_ids != set(ids):
        raise Http404
    return ids


def _usable_email(value):
    if not value:
        return ""
    try:
        return forms.EmailField().clean(value)
    except ValidationError:
        return ""


class ProgramMailMergeView(LoginRequiredMixin, View):
    template_name = "programs/mail_merge.html"
    organization: Organization
    role: str

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        organization = getattr(request, "organization", None)
        if not isinstance(organization, Organization):
            raise Http404
        self.organization = organization

        self.role = get_user_role(request.user, org=self.organization)
        if self.role not in ("Mentor", "LeadMentor") or (
            self.role == "Mentor"
            and not can_user_read(request.user, "mail_merge", org=self.organization)
        ):
            messages.error(request, "You do not have permission to use mail merge.")
            return redirect("home")
        return super().dispatch(request, *args, **kwargs)

    def _accessible_programs(self):
        programs = Program.objects.for_organization(self.organization).order_by(
            "-active", "name"
        )
        if self.role == "LeadMentor":
            return list(programs)
        if self.role != "Mentor":
            return []
        program_access = (
            RolePermission.objects.for_organization(self.organization)
            .filter(role="Mentor", section="programs")
            .values_list("can_read", flat=True)
            .first()
        )
        if program_access is False:
            return []
        return [
            program
            for program in programs.filter(active=True)
            if program.status in ("Active", "Upcoming")
        ]

    def _resolve_program(self, request, query_data, programs) -> Program | None:
        program_id = query_data.get("program")
        if not program_id and programs:
            return programs[0]
        if not program_id:
            return None
        try:
            program = next(
                program for program in programs if str(program.pk) == program_id
            )
        except StopIteration:
            raise Http404
        if not can_user_mail_merge(request.user, program, org=self.organization):
            raise Http404
        return program

    def _subgroups(self, program):
        if program is None:
            return {
                "teams": Team.objects.none(),
                "crews": Crew.objects.none(),
                "subteams": SubTeam.objects.none(),
            }
        active_enrollments = Enrollment.objects.filter(
            organization=self.organization,
            program=program,
            program__organization=self.organization,
            student__organization=self.organization,
            active=True,
            student__graduated=False,
        )
        return {
            "teams": Team.objects.for_organization(self.organization)
            .filter(enrollments__in=active_enrollments)
            .distinct()
            .order_by("team_type", "number"),
            "crews": Crew.objects.for_organization(self.organization)
            .filter(program=program, program__organization=self.organization)
            .filter(enrollments__in=active_enrollments)
            .distinct()
            .order_by("name"),
            "subteams": SubTeam.objects.for_organization(self.organization)
            .filter(program=program, program__organization=self.organization)
            .filter(enrollments__in=active_enrollments)
            .distinct()
            .order_by("name"),
        }

    def _enrollments(self, program, subgroup_query_data, subgroups):
        team_ids = _get_selected_ids(subgroup_query_data, "teams", subgroups["teams"])
        crew_ids = _get_selected_ids(subgroup_query_data, "crews", subgroups["crews"])
        subteam_ids = _get_selected_ids(
            subgroup_query_data, "subteams", subgroups["subteams"]
        )
        if program is None:
            return Enrollment.objects.none(), team_ids, crew_ids, subteam_ids

        enrollments = (
            Enrollment.objects.for_organization(self.organization)
            .filter(
                organization=self.organization,
                program=program,
                program__organization=self.organization,
                student__organization=self.organization,
                active=True,
                student__graduated=False,
            )
            .filter(Q(team__isnull=True) | Q(team__organization=self.organization))
            .filter(
                Q(crew__isnull=True)
                | Q(crew__organization=self.organization, crew__program=program)
            )
            .filter(
                Q(subteam__isnull=True)
                | Q(subteam__organization=self.organization, subteam__program=program)
            )
        )
        if team_ids:
            enrollments = enrollments.filter(team_id__in=team_ids)
        if crew_ids:
            enrollments = enrollments.filter(crew_id__in=crew_ids)
        if subteam_ids:
            enrollments = enrollments.filter(subteam_id__in=subteam_ids)
        return (
            enrollments.select_related("student", "team", "crew", "subteam").order_by(
                Lower(
                    Coalesce(
                        NullIf("student__preferred_first_name", Value("")),
                        "student__legal_first_name",
                    )
                ),
                Lower("student__last_name"),
                "student_id",
            ),
            team_ids,
            crew_ids,
            subteam_ids,
        )

    def _field_labels(self, request, program: Program | None):
        labels = dict(BASE_FIELD_LABELS)
        if program is None:
            return labels, False, False
        feature_keys = program.feature_keys
        attendance_allowed = bool(
            "attendance" in feature_keys
            and can_user_read(
                request.user, "attendance", program, org=self.organization
            )
        )
        outreach_allowed = bool(
            "outreach" in feature_keys
            and can_user_read(request.user, "outreach", program, org=self.organization)
        )
        if attendance_allowed:
            labels.update(ATTENDANCE_FIELD_LABELS)
        if outreach_allowed:
            labels.update(OUTREACH_FIELD_LABELS)
        return labels, attendance_allowed, outreach_allowed

    def _recipient_mode(self, query_data):
        value = query_data.get("recipients", "students")
        return value if value in ("students", "students_and_parents") else "students"

    def _parent_emails_by_student(self, student_ids):
        if not student_ids:
            return {}
        relationships = (
            AdultStudentRelationship.objects.for_organization(self.organization)
            .filter(
                organization=self.organization,
                student_id__in=student_ids,
                student__organization=self.organization,
                adult__organization=self.organization,
                adult__email_updates=True,
                adult__login_enabled=True,
            )
            .values_list("student_id", "adult__personal_email", "adult__org_email")
            .order_by("student_id", "adult_id")
        )
        emails_by_student = {}
        for student_id, personal_email, org_email in relationships:
            email = _usable_email(personal_email) or _usable_email(org_email)
            if not email:
                continue
            emails = emails_by_student.setdefault(student_id, [])
            if email.casefold() not in {value.casefold() for value in emails}:
                emails.append(email)
        return emails_by_student

    def _attach_recipients(self, merge_rows, recipient_mode):
        student_ids = [row["student"].pk for row in merge_rows]
        parent_emails_by_student = (
            self._parent_emails_by_student(student_ids)
            if recipient_mode == "students_and_parents"
            else {}
        )
        for row in merge_rows:
            student_email = row["values"].get("email", "")
            parent_emails = [
                email
                for email in parent_emails_by_student.get(row["student"].pk, [])
                if email.casefold() != student_email.casefold()
            ]
            row["student_email"] = student_email
            row["parent_emails"] = parent_emails
            recipients_by_address = {}
            for email in [student_email, *parent_emails]:
                if email:
                    recipients_by_address.setdefault(email.casefold(), email)
            row["recipients"] = list(recipients_by_address.values())

    def _student_fields(
        self,
        program: Program | None,
        enrollments,
        custom_rows,
        *,
        submitted_rows=False,
        attendance_allowed=False,
        outreach_allowed=False,
    ):
        selected_ids = [row["student_id"] for row in custom_rows]
        if program is None:
            if submitted_rows and selected_ids:
                raise ValidationError("Select a program before selecting students.")
            return [], dict(BASE_FIELD_LABELS)
        if submitted_rows:
            if len(selected_ids) > MAX_MERGE_ROWS:
                raise ValidationError(
                    f"Select no more than {MAX_MERGE_ROWS} students at a time."
                )
            enrollments = list(enrollments.filter(student_id__in=selected_ids))
            enrollment_by_id = {
                enrollment.student_id: enrollment for enrollment in enrollments
            }
            if any(student_id not in enrollment_by_id for student_id in selected_ids):
                # A forged or stale row ID must never be treated as a recipient.
                raise ValidationError(
                    "A selected student is no longer in this program and filter selection. Click Load students to refresh the list, then try again."
                )
            enrollments = [enrollment_by_id[student_id] for student_id in selected_ids]
        else:
            enrollments = list(enrollments[: MAX_MERGE_ROWS + 1])
            if len(enrollments) > MAX_MERGE_ROWS:
                enrollments = enrollments[:MAX_MERGE_ROWS]

        student_ids = [enrollment.student_id for enrollment in enrollments]
        attendance_by_student = {}
        if attendance_allowed and student_ids:
            today = timezone.localdate()
            end_date = (
                program.end_date
                if program.end_date and program.end_date < today
                else today
            )
            sessions = AttendanceSession.objects.for_organization(
                self.organization
            ).filter(
                organization=self.organization,
                program=program,
                program__organization=self.organization,
                student_id__in=student_ids,
                student__organization=self.organization,
                check_in__date__lte=end_date,
            )
            start_date = program.start_date
            if start_date is None:
                earliest = (
                    sessions.order_by("check_in")
                    .values_list("check_in", flat=True)
                    .first()
                )
                start_date = earliest.date() if earliest else None
            if start_date:
                sessions = sessions.filter(check_in__date__gte=start_date)
                weeks_elapsed = max(((end_date - start_date).days + 6) // 7, 1)
            else:
                weeks_elapsed = 1
            attendance_by_student = {
                stat["id"]: stat
                for stat in get_attendance_stats_by_student(sessions, weeks_elapsed)
            }

        outreach_by_student = (
            get_outreach_stats_by_student(student_ids, program, self.organization)
            if outreach_allowed and student_ids
            else {}
        )
        custom_rows_by_id = {row["student_id"]: row for row in custom_rows}
        result = []
        for enrollment in enrollments:
            student = enrollment.student
            first_name = student.preferred_first_name or student.legal_first_name
            email = _usable_email(student.personal_email or student.org_email or "")
            team_name = (
                enrollment.team.name
                if enrollment.team
                and enrollment.team.organization_id == self.organization.pk
                else ""
            )
            crew_name = (
                enrollment.crew.name
                if enrollment.crew
                and enrollment.crew.organization_id == self.organization.pk
                and enrollment.crew.program_id == program.pk
                else ""
            )
            subteam_name = (
                enrollment.subteam.name
                if enrollment.subteam
                and enrollment.subteam.organization_id == self.organization.pk
                and enrollment.subteam.program_id == program.pk
                else ""
            )
            values = {
                "first_name": first_name,
                "last_name": student.last_name,
                "full_name": " ".join(
                    part for part in (first_name, student.last_name) if part
                ),
                "email": email,
                "program_name": program.name,
                "team_name": team_name,
                "crew_name": crew_name,
                "project_name": crew_name,
                "subteam_name": subteam_name,
            }
            attendance = attendance_by_student.get(student.pk, {})
            if attendance_allowed:
                values.update(
                    {
                        "attendance_total_hours": f"{attendance.get('total_hours', 0):.1f}",
                        "attendance_average_hours_per_week": f"{attendance.get('avg_per_week', 0):.1f}",
                        "attendance_sessions_count": attendance.get("session_count", 0),
                        "attendance_days_attended": attendance.get("days", 0),
                        "attendance_last_attended": (
                            timezone.localtime(attendance["last_attended"])
                            .date()
                            .isoformat()
                            if attendance.get("last_attended")
                            else ""
                        ),
                    }
                )
            outreach = outreach_by_student.get(student.pk, {})
            if outreach_allowed:
                values.update(
                    {
                        "outreach_total_hours": f"{outreach.get('total_outreach_hours', 0):.1f}",
                        "outreach_pending_hours": f"{outreach.get('pending_outreach_hours', 0):.1f}",
                        "outreach_shifts_attended": outreach.get("shifts_attended", 0),
                        "outreach_shifts_signed_up": outreach.get(
                            "shifts_signed_up", 0
                        ),
                        "outreach_events_championed": outreach.get(
                            "championed_count", 0
                        ),
                        "outreach_shifts_unconfirmed": outreach.get(
                            "unconfirmed_count", 0
                        ),
                    }
                )
            custom_row = custom_rows_by_id.get(student.pk, {})
            values.update(
                {
                    column: custom_row.get(column, "")
                    for column in custom_row
                    if column != "student_id"
                }
            )
            result.append(
                {
                    "student": student,
                    "enrollment": enrollment,
                    "values": values,
                    "custom_values": custom_rows_by_id.get(student.pk, {}),
                }
            )
        available_labels = {
            **BASE_FIELD_LABELS,
            **(ATTENDANCE_FIELD_LABELS if attendance_allowed else {}),
            **(OUTREACH_FIELD_LABELS if outreach_allowed else {}),
        }
        return result, available_labels

    def _render(self, request, *, action="", merge_data=None, error=""):
        query_data = request.POST if request.method == "POST" else request.GET
        recipient_mode = self._recipient_mode(query_data)
        programs = self._accessible_programs()
        program = self._resolve_program(request, query_data, programs)
        subgroups = self._subgroups(program)
        try:
            enrollments, team_ids, crew_ids, subteam_ids = self._enrollments(
                program,
                query_data,
                subgroups,
            )
        except Http404:
            raise

        raw_merge_data = merge_data or {"columns": [], "rows": []}
        custom_rows = raw_merge_data["rows"]
        subject = request.POST.get("subject", "") if request.method == "POST" else ""
        body = request.POST.get("body", "") if request.method == "POST" else ""
        test_email = (
            request.POST.get("test_email", "") if request.method == "POST" else ""
        )
        preview_index = (
            request.POST.get("preview_index", "0") if request.method == "POST" else "0"
        )
        try:
            preview_index = max(0, int(preview_index))
        except (TypeError, ValueError):
            preview_index = 0

        field_labels, attendance_allowed, outreach_allowed = self._field_labels(
            request, program
        )
        try:
            merge_rows, available_labels = self._student_fields(
                program,
                enrollments,
                custom_rows,
                submitted_rows=request.method == "POST" and merge_data is not None,
                attendance_allowed=attendance_allowed,
                outreach_allowed=outreach_allowed,
            )
        except ValidationError as exc:
            error = "; ".join(exc.messages)
            merge_rows, available_labels = [], field_labels
        self._attach_recipients(merge_rows, recipient_mode)
        if merge_rows:
            preview_index = min(preview_index, len(merge_rows) - 1)
        grid_fields = [
            {
                "key": key,
                "label": label,
                "group": _grid_column_group(key),
                "sticky": key in STICKY_GRID_FIELDS,
            }
            for key, label in available_labels.items()
            if key not in GRID_HIDDEN_FIELDS
        ]
        present_groups = {field["group"] for field in grid_fields}
        grid_column_groups = [
            {"key": key, "label": label}
            for key, label in GRID_COLUMN_GROUPS
            if key in present_groups
        ]
        available_labels.update(
            {
                column: column.replace("_", " ").title()
                for column in raw_merge_data["columns"]
            }
        )
        for row in merge_rows:
            row["grid_cells"] = [
                {**field, "value": row["values"].get(field["key"], "")}
                for field in grid_fields
            ]
            row["custom_cells"] = [
                {
                    "key": column,
                    "value": row["values"].get(column, ""),
                }
                for column in raw_merge_data["columns"]
            ]
        selected_fields = list(available_labels.items())
        tag_report = {"unknown": [], "split": []}
        if subject or body:
            subject_report = analyze_merge_fields(subject, available_labels)
            body_report = analyze_merge_fields(body, available_labels)
            tag_report = {
                "unknown": list(
                    dict.fromkeys(subject_report["unknown"] + body_report["unknown"])
                ),
                "split": body_report["split"],
            }

        preview_html = ""
        preview_text = ""
        preview_subject = ""
        blank_fields = []
        if merge_rows and preview_index < len(merge_rows) and subject and body:
            preview_row = merge_rows[preview_index]
            row_values = preview_row["values"]
            template_html, template_text, _ = build_email_parts(
                body, organization=self.organization, subject=subject
            )
            preview_html = substitute_html_fields(template_html, row_values)
            preview_text = substitute_text_fields(template_text, row_values)
            preview_subject = substitute_subject(subject, row_values)
            used_fields = set(
                re.findall(r"{{\s*([a-z][a-z0-9_]*)\s*}}", subject + " " + body)
            )
            blank_fields = sorted(
                field
                for field in used_fields
                if field in row_values and not row_values[field]
            )

        sender_form = ProgramEmailForm(
            user=request.user,
            program=program,
            organization=self.organization,
        )
        sender_field = sender_form.fields["from_account"]
        if isinstance(sender_field, forms.ChoiceField):
            sender_choices = list(sender_field.choices)
        else:
            sender_choices = []
        context = {
            "programs": programs,
            "program": program,
            "teams": subgroups["teams"],
            "crews": subgroups["crews"],
            "subteams": subgroups["subteams"],
            "selected_teams": [str(value) for value in team_ids],
            "selected_crews": [str(value) for value in crew_ids],
            "selected_subteams": [str(value) for value in subteam_ids],
            "merge_rows": merge_rows,
            "columns": raw_merge_data["columns"],
            "grid_fields": grid_fields,
            "grid_column_groups": grid_column_groups,
            "merge_data_json": json.dumps(raw_merge_data),
            "subject": subject,
            "body": body,
            "test_email": test_email,
            "preview_index": preview_index,
            "preview_html": preview_html,
            "preview_text": preview_text,
            "preview_subject": preview_subject,
            "blank_fields": blank_fields,
            "tag_report": tag_report,
            "field_options": selected_fields,
            "reserved_fields_json": json.dumps(sorted(BUILT_IN_FIELDS)),
            "max_merge_rows": MAX_MERGE_ROWS,
            "sender_choices": sender_choices,
            "sender_value": request.POST.get("from_account")
            or sender_form.fields["from_account"].initial,
            "action": action,
            "error": error,
            "recipient_mode": recipient_mode,
            "student_limit_exceeded": enrollments.count() > MAX_MERGE_ROWS,
            "student_recipient_count": sum(
                bool(row["student_email"]) for row in merge_rows
            ),
            "parent_recipient_count": sum(
                len(row["parent_emails"]) for row in merge_rows
            ),
            "recipient_count": sum(len(row["recipients"]) for row in merge_rows),
            "no_email_count": sum(not bool(row["student_email"]) for row in merge_rows),
        }
        return render(request, self.template_name, context)

    def get(self, request):
        return self._render(request)

    def post(self, request):
        action = request.POST.get("action", "preview")
        try:
            merge_data = parse_merge_data(request.POST.get("merge_data", ""))
        except ValidationError as exc:
            return self._render(
                request,
                action=action,
                merge_data={"columns": [], "rows": []},
                error="; ".join(exc.messages),
            )

        if action not in {"confirm", "send", "test_send"}:
            return self._render(request, action=action, merge_data=merge_data)

        programs = self._accessible_programs()
        program = self._resolve_program(request, request.POST, programs)
        if program is None:
            return self._render(
                request, action=action, merge_data=merge_data, error="Select a program."
            )
        if not can_user_mail_merge(request.user, program, org=self.organization):
            raise Http404
        _, attendance_allowed, outreach_allowed = self._field_labels(request, program)
        subgroups = self._subgroups(program)
        enrollments, _, _, _ = self._enrollments(program, request.POST, subgroups)
        try:
            merge_rows, available_labels = self._student_fields(
                program,
                enrollments,
                merge_data["rows"],
                submitted_rows=True,
                attendance_allowed=attendance_allowed,
                outreach_allowed=outreach_allowed,
            )
        except ValidationError as exc:
            return self._render(
                request,
                action=action,
                merge_data=merge_data,
                error="; ".join(exc.messages),
            )

        recipient_mode = self._recipient_mode(request.POST)
        self._attach_recipients(merge_rows, recipient_mode)
        if not merge_rows:
            return self._render(
                request,
                action=action,
                merge_data=merge_data,
                error="Select at least one student. Tick the box next to each student who should get the email.",
            )
        available_labels.update(
            {
                column: column.replace("_", " ").title()
                for column in merge_data["columns"]
            }
        )
        subject = (request.POST.get("subject") or "").strip()
        body = request.POST.get("body") or ""
        if not subject or not body.strip():
            return self._render(
                request,
                action=action,
                merge_data=merge_data,
                error="Enter both a subject and an email message.",
            )
        if len(subject) > 255:
            return self._render(
                request,
                action=action,
                merge_data=merge_data,
                error="The subject must be no more than 255 characters.",
            )

        subject_report = analyze_merge_fields(subject, available_labels)
        body_report = analyze_merge_fields(body, available_labels)
        if subject_report["unknown"] or body_report["unknown"] or body_report["split"]:
            return self._render(
                request,
                action=action,
                merge_data=merge_data,
                error="Fix unknown or formatting-split merge fields before testing or sending. See the yellow warnings below the message for which placeholders need attention.",
            )

        if action == "confirm":
            recipient_count = sum(len(row["recipients"]) for row in merge_rows)
            if not recipient_count:
                return self._render(
                    request,
                    action=action,
                    merge_data=merge_data,
                    error="None of the selected students have a usable email address on file. Check their profiles, or choose Students and parents to email parents instead.",
                )
            return self._render(request, action="confirm", merge_data=merge_data)

        if action == "test_send":
            try:
                test_email = forms.EmailField().clean(
                    request.POST.get("test_email", "")
                )
                preview_index = int(request.POST.get("preview_index", "0"))
                preview_index = max(0, min(preview_index, len(merge_rows) - 1))
            except (ValidationError, TypeError, ValueError):
                return self._render(
                    request,
                    action=action,
                    merge_data=merge_data,
                    error="Enter a valid email address in Send test to, then try the test again.",
                )
            target_rows = [(merge_rows[preview_index], test_email)]
        elif action == "send":
            target_rows = [
                (row, recipient)
                for row in merge_rows
                for recipient in row["recipients"]
            ]
            if not target_rows:
                return self._render(
                    request,
                    action=action,
                    merge_data=merge_data,
                    error="None of the selected students have a usable email address on file. Check their profiles, or choose Students and parents to email parents instead.",
                )
        else:
            return self._render(
                request,
                action=action,
                merge_data=merge_data,
                error="Unknown mail merge action.",
            )

        sender_form = ProgramEmailForm(
            user=request.user,
            program=program,
            organization=self.organization,
        )
        try:
            sender = sender_form.fields["from_account"].clean(
                request.POST.get("from_account", "DEFAULT")
            )
        except ValidationError:
            return self._render(
                request,
                action=action,
                merge_data=merge_data,
                error="Choose an option from the Send from list.",
            )
        connection, from_email, reply_to = get_sender_connection(
            sender, organization=self.organization
        )

        template_html, template_text, inline_images = build_email_parts(
            body, organization=self.organization, subject=subject
        )
        messages_to_send = []
        for row, recipient in target_rows:
            row_values = row["values"]
            email = EmailMultiAlternatives(
                subject=substitute_subject(subject, row_values),
                body=substitute_text_fields(template_text, row_values),
                from_email=from_email,
                to=[recipient],
                reply_to=[reply_to] if reply_to else None,
                connection=connection,
            )
            email.attach_alternative(
                substitute_html_fields(template_html, row_values), "text/html"
            )
            for inline_image in inline_images:
                email.attach(inline_image.to_mime())
            _mark_delivery_tracking(
                email,
                self.organization,
                enabled=action == "send",
            )
            messages_to_send.append(email)

        logger.info(
            "ProgramMailMerge: dispatch requested | organization_id=%s | "
            "user_id=%s | recipient_count=%d | custom_columns=%s | test=%s",
            self.organization.pk,
            request.user.pk,
            len(messages_to_send),
            ",".join(merge_data["columns"]),
            action == "test_send",
        )
        try:
            send_messages_in_background(messages_to_send, connection=connection)
        except Exception as exc:
            logger.error(
                "ProgramMailMerge: dispatch failed | recipient_count=%d | error=%s",
                len(messages_to_send),
                type(exc).__name__,
            )
            return self._render(
                request,
                action=action,
                merge_data=merge_data,
                error="The email dispatch could not be queued. Please try again.",
            )

        if action == "test_send":
            messages.success(request, "Test email queued for the selected preview row.")
            return self._render(request, action="preview", merge_data=merge_data)

        messages.success(
            request,
            f"Personalized email queued for {len(messages_to_send)} recipient(s).",
        )
        return redirect(f"{reverse('program_mail_merge')}?program={program.pk}")
