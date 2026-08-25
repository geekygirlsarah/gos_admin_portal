"""Lead-mentor review pages for applications.

Provides custom views (list / detail / approve / decline / edit / delete)
gated by the ``applications.review_application`` permission. The
``LeadMentor`` group (unified in migration ``0011``) carries that permission.
"""

from __future__ import annotations

import datetime
import logging

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import SuspiciousFileOperation
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import models
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.html import strip_tags
from django.views import View
from premailer import transform

from audit.events import AuditEvent
from audit.service import log_event
from programs.constants import (
    GRADE_CHOICES,
    PHONE_TYPE_CHOICES,
    RELATIONSHIP_CHOICES,
    STATE_CHOICES,
    TSHIRT_SIZE_CHOICES,
)
from programs.models import Program, RaceEthnicity

from ..forms import StaffDocumentUploadForm
from ..models import Application, ApplicationDocumentSubmission
from ..services import (
    ApplicationConversionError,
    _collect_applicant_recipients,
    convert_application_to_student,
    get_primary_parent_email,
    send_application_approved_email,
    send_application_converted_email,
    send_application_declined_email,
    send_application_submitted_email,
    send_otp_email,
    send_parent_handoff_email,
)

logger = logging.getLogger(__name__)

REVIEW_PERM = "applications.review_application"


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------


class DeclineForm(forms.Form):
    """Captures the reason the lead mentors are declining an application.

    The reason is emailed to the applicant (along with the parent, when
    applicable) so they understand why and can follow up.
    """

    reason = forms.CharField(
        label="Reason (will be emailed to the applicant)",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 5,
                "placeholder": "Briefly explain why this application is being declined.",
            }
        ),
        required=False,
        help_text=(
            "Optional. If provided, this text will appear in the email "
            "sent to the applicant."
        ),
    )


# ---------------------------------------------------------------------------
# Per-field edit of the captured wizard ``data``.
#
# The wizard stores each step's cleaned data as a top-level key in
# ``Application.data`` (e.g. ``step5-student``). Instead of asking lead
# mentors to hand-edit JSON, we build a form with one field per captured
# value, grouped by step and laid out in application order. Fields that
# start with ``_`` (e.g. ``_existing_student_id``) are internal and are
# preserved on save but never shown.
# ---------------------------------------------------------------------------


_EDIT_STATE = [("", "---")] + list(STATE_CHOICES)
_EDIT_PHONE = list(PHONE_TYPE_CHOICES)
_EDIT_RELATIONSHIP = [("", "---")] + list(RELATIONSHIP_CHOICES)
_EDIT_TSHIRT = [("", "---")] + list(TSHIRT_SIZE_CHOICES)
_EDIT_GRADE = [("", "—")] + [(str(v), label) for v, label in GRADE_CHOICES]
_CLEARANCE_INTEREST = [
    ("", "---"),
    ("yes", "Yes, I want to start / complete clearances."),
    ("no", "No, not at this time."),
]
_CLEARANCE_STATUS = [
    ("", "---"),
    ("have", "I already have this clearance."),
    ("need", "I don't have this yet — I'll need to get it."),
]


_STUDENT_FIELDS = [
    {"name": "legal_first_name", "label": "Legal first name", "kind": "text"},
    {
        "name": "first_name",
        "label": "Preferred first name (if different)",
        "kind": "text",
    },
    {"name": "last_name", "label": "Last name", "kind": "text"},
    {"name": "pronouns", "label": "Pronouns", "kind": "text"},
    {"name": "date_of_birth", "label": "Date of birth", "kind": "date"},
    {"name": "address", "label": "Address", "kind": "text"},
    {"name": "city", "label": "City", "kind": "text"},
    {"name": "state", "label": "State", "kind": "select", "choices": _EDIT_STATE},
    {"name": "zip_code", "label": "Zip code", "kind": "text"},
    {"name": "personal_email", "label": "Student's personal email", "kind": "email"},
    {"name": "phone_number", "label": "Student's phone number", "kind": "text"},
    {
        "name": "phone_type",
        "label": "Phone type",
        "kind": "select",
        "choices": _EDIT_PHONE,
    },
    {"name": "can_receive_texts", "label": "Can receive texts?", "kind": "checkbox"},
    {
        "name": "directory_consent",
        "label": "OK to share name, address, and phone for directory / carpool map",
        "kind": "checkbox",
        "initial": True,
    },
    {"name": "school_name", "label": "School", "kind": "text"},
    {
        "name": "grade",
        "label": "Grade (K–12)",
        "kind": "select",
        "choices": _EDIT_GRADE,
    },
    {
        "name": "graduation_year",
        "label": "Expected graduation year",
        "kind": "int",
    },
    {"name": "race_ethnicities", "label": "Race / Ethnicity", "kind": "multi"},
    {
        "name": "tshirt_size",
        "label": "T-shirt size",
        "kind": "select",
        "choices": _EDIT_TSHIRT,
    },
    {"name": "allergies", "label": "Allergies", "kind": "textarea"},
    {
        "name": "dietary_restrictions",
        "label": "Dietary restrictions",
        "kind": "textarea",
    },
    {"name": "medical_notes", "label": "Other medical notes", "kind": "textarea"},
]

_EXPERIENCE_FIELDS = [
    {
        "name": "interest_reason",
        "label": "Why are you interested in this program this season?",
        "kind": "textarea",
    },
    {
        "name": "hoped_gains",
        "label": "What do you hope to gain from the experience?",
        "kind": "textarea",
    },
    {
        "name": "prior_robotics_experience",
        "label": "What prior robotics experience do you have?",
        "kind": "textarea",
    },
    {
        "name": "referral_source",
        "label": "How did you hear about Girls of Steel Robotics?",
        "kind": "textarea",
    },
]

_PARENT_FIELDS = [
    {"name": "first_name", "label": "Legal first name", "kind": "text"},
    {
        "name": "preferred_first_name",
        "label": "Preferred first name (if different)",
        "kind": "text",
    },
    {"name": "last_name", "label": "Last name", "kind": "text"},
    {"name": "pronouns", "label": "Pronouns", "kind": "text"},
    {
        "name": "relationship_to_student",
        "label": "Relationship to student",
        "kind": "select",
        "choices": _EDIT_RELATIONSHIP,
    },
    {
        "name": "specific_relationship",
        "label": "Specific relationship",
        "kind": "text",
    },
    {"name": "email", "label": "Email address", "kind": "email"},
    {"name": "address", "label": "Address", "kind": "text"},
    {"name": "city", "label": "City", "kind": "text"},
    {"name": "state", "label": "State", "kind": "select", "choices": _EDIT_STATE},
    {"name": "zip_code", "label": "Zip code", "kind": "text"},
    {"name": "phone_number", "label": "Phone number", "kind": "text"},
    {
        "name": "phone_type",
        "label": "Phone type",
        "kind": "select",
        "choices": _EDIT_PHONE,
    },
    {
        "name": "can_receive_texts",
        "label": "Can receive texts?",
        "kind": "checkbox",
        "initial": True,
    },
    {"name": "email_updates", "label": "Receive email updates", "kind": "checkbox"},
]

_HANDOFF_FIELDS = [
    {"name": "parent_email", "label": "Parent / guardian email", "kind": "email"},
]

_MENTOR_FIELDS = [
    {"name": "legal_first_name", "label": "Legal first name", "kind": "text"},
    {
        "name": "first_name",
        "label": "Preferred first name (if different)",
        "kind": "text",
    },
    {"name": "last_name", "label": "Last name", "kind": "text"},
    {"name": "pronouns", "label": "Pronouns", "kind": "text"},
    {"name": "phone_number", "label": "Phone number", "kind": "text"},
    {
        "name": "phone_type",
        "label": "Phone type",
        "kind": "select",
        "choices": _EDIT_PHONE,
    },
    {
        "name": "can_receive_texts",
        "label": "Can receive texts?",
        "kind": "checkbox",
        "initial": True,
    },
    {"name": "andrew_id", "label": "Andrew ID (if you have one)", "kind": "text"},
    {"name": "employer", "label": "Employer / affiliation", "kind": "text"},
    {
        "name": "notes",
        "label": "Why are you interested in volunteering?",
        "kind": "textarea",
    },
]

_CLEARANCE_INTEREST_FIELDS = [
    {
        "name": "interested",
        "label": "Interested in obtaining PA child-protection clearances?",
        "kind": "select",
        "choices": _CLEARANCE_INTEREST,
    },
]

_CLEARANCE_DETAIL_FIELDS = [
    {
        "name": "paca",
        "label": "PA Child Abuse Clearance (PACA)",
        "kind": "select",
        "choices": _CLEARANCE_STATUS,
    },
    {
        "name": "patch",
        "label": "PA Criminal Record Clearance (PATCH)",
        "kind": "select",
        "choices": _CLEARANCE_STATUS,
    },
    {
        "name": "fbi",
        "label": "FBI criminal fingerprint check",
        "kind": "select",
        "choices": _CLEARANCE_STATUS,
    },
]

# (data key, section heading, [field specs]) — in application order.
STUDENT_SECTIONS = [
    ("step5-student", "Student information", _STUDENT_FIELDS),
    ("step6-experience", "Student experience", _EXPERIENCE_FIELDS),
    ("step7-primaryparent", "Primary parent / guardian", _PARENT_FIELDS),
    ("step8-secondaryparent", "Secondary parent / guardian", _PARENT_FIELDS),
    ("step7_handoff", "Parent handoff", _HANDOFF_FIELDS),
]

MENTOR_SECTIONS = [
    ("mentor_info", "Mentor information", _MENTOR_FIELDS),
    ("mentor_clearance_interest", "Clearance interest", _CLEARANCE_INTEREST_FIELDS),
    ("mentor_clearance_detail", "Clearance details", _CLEARANCE_DETAIL_FIELDS),
]


def _sections_for(application) -> list:
    if application.applicant_type == Application.Type.MENTOR:
        return MENTOR_SECTIONS
    return STUDENT_SECTIONS


class ApplicationDataEditForm(forms.Form):
    """Per-field edit of the wizard's captured ``data`` JSON.

    Renders every value the wizard captures, grouped by step and laid out
    in application order, so lead mentors can fix typos on behalf of an
    applicant without hand-editing JSON. Internal keys (prefixed with ``_``)
    are preserved on save and never surfaced.

    The top-level ``email`` field edits the primary contact email on the
    Application model (separate from the email fields captured inside each
    wizard step).
    """

    email = forms.EmailField(
        label="Primary contact email",
        required=False,
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )

    def __init__(self, *args, sections=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.sections = list(sections)
        for _data_key, _title, fields in self.sections:
            for spec in fields:
                self.fields[self._field_name(_data_key, spec["name"])] = (
                    self._make_field(spec)
                )

    @staticmethod
    def _field_name(data_key, field_name):
        return f"{data_key}__{field_name}"

    @staticmethod
    def _make_field(spec):
        kind = spec["kind"]
        label = spec.get("label")
        help_text = spec.get("help_text", "")
        required = False
        attrs = {"class": "form-control"}
        if kind == "textarea":
            attrs["rows"] = 3
            field = forms.CharField(
                label=label,
                required=required,
                help_text=help_text,
                widget=forms.Textarea(attrs=attrs),
            )
        elif kind == "email":
            field = forms.EmailField(
                label=label,
                required=required,
                help_text=help_text,
                widget=forms.EmailInput(attrs=attrs),
            )
        elif kind == "date":
            attrs["type"] = "date"
            field = forms.DateField(
                label=label,
                required=required,
                help_text=help_text,
                widget=forms.DateInput(attrs=attrs, format="%Y-%m-%d"),
            )
        elif kind == "int":
            field = forms.IntegerField(
                label=label,
                required=required,
                help_text=help_text,
                widget=forms.TextInput(attrs=attrs),
            )
        elif kind == "select":
            field = forms.ChoiceField(
                label=label,
                required=required,
                help_text=help_text,
                choices=spec["choices"],
                widget=forms.Select(attrs={"class": "form-select"}),
            )
        elif kind == "checkbox":
            field = forms.BooleanField(
                label=label,
                required=False,
                help_text=help_text,
                widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
            )
        elif kind == "multi":
            field = forms.ModelMultipleChoiceField(
                label=label,
                required=required,
                help_text=help_text,
                queryset=RaceEthnicity.objects.all().order_by("name"),
                widget=forms.CheckboxSelectMultiple(
                    attrs={"class": "form-check-input"}
                ),
            )
        else:  # "text"
            field = forms.CharField(
                label=label,
                required=required,
                help_text=help_text,
                widget=forms.TextInput(attrs=attrs),
            )
        if spec.get("initial") is not None:
            field.initial = spec["initial"]
        return field

    def rebuild_data(self, current_data):
        """Rebuild ``Application.data`` from submitted fields.

        Only the fields represented in this form are overwritten; any other
        keys already stored for a step (e.g. ``_existing_student_id``) are
        preserved. Empty sections are only written back if they already
        existed or the mentor actually entered a value.
        """
        current_data = dict(current_data or {})
        data = dict(current_data)
        for data_key, _title, fields in self.sections:
            existing = current_data.get(data_key)
            step = dict(existing) if isinstance(existing, dict) else {}
            touched = False
            for spec in fields:
                fname = spec["name"]
                field_name = self._field_name(data_key, fname)
                if field_name in self.cleaned_data:
                    value = self.cleaned_data[field_name]
                    if spec["kind"] == "multi":
                        value = list(value.values_list("pk", flat=True))
                    elif hasattr(value, "isoformat"):
                        value = value.isoformat()
                    step[fname] = value
                    if value not in (None, "", [], False):
                        touched = True
            if existing or touched:
                data[data_key] = step
        return data


class ApplicationEmailForm(forms.Form):
    program = forms.ModelChoiceField(
        queryset=Program.objects.all(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text="Select the program whose applicants you want to email. Leave blank for all programs.",
    )
    statuses = forms.MultipleChoiceField(
        required=True,
        choices=Application.Status.choices,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
        help_text="Choose one or more application statuses to email.",
    )
    subject = forms.CharField(
        max_length=255, widget=forms.TextInput(attrs={"class": "form-control"})
    )
    body = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 12, "class": "form-control"}),
        help_text="Rich text is supported. Paste content or use the editor.",
    )
    test_email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={"class": "form-control"}),
        help_text="Optional: send only to this address for testing.",
    )
    from_account = forms.ChoiceField(
        required=False, widget=forms.Select(attrs={"class": "form-select"})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Build sender choices from settings
        accounts = getattr(settings, "EMAIL_SENDER_ACCOUNTS", []) or []
        choices = []
        initial_value = None
        if accounts:
            for acc in accounts:
                email = acc.get("email") or ""
                display = acc.get("display_name") or email or "Sender"
                value = acc.get("key") or email
                label = f"{display} <{email}>" if email else display
                choices.append((value, label))
            if choices:
                initial_value = choices[0][0]
        else:
            default_email = getattr(settings, "DEFAULT_FROM_EMAIL", "")
            default_name = getattr(settings, "DEFAULT_FROM_NAME", None)
            if default_name:
                label = (
                    f"Default ({default_name} <{default_email}>)"
                    if default_email
                    else f"Default ({default_name})"
                )
            else:
                label = f"Default ({default_email})" if default_email else "Default"
            choices = [("DEFAULT", label)]
            initial_value = "DEFAULT"

        self.fields["from_account"].choices = choices
        self.fields["from_account"].initial = initial_value


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class _ReviewerRequiredMixin(LoginRequiredMixin, PermissionRequiredMixin):
    """All review pages require the dedicated review permission."""

    permission_required = REVIEW_PERM
    raise_exception = False  # default behavior: redirect to login


@method_decorator(login_required, name="dispatch")
class ApplicationReviewListView(_ReviewerRequiredMixin, View):
    """List page with status + applicant-type + program filters."""

    template_name = "applications/review/list.html"

    def get(self, request):
        qs = Application.objects.select_related("program").all()
        status = (request.GET.get("status") or "").strip()
        applicant_type = (request.GET.get("type") or "").strip()
        program_id = (request.GET.get("program") or "").strip()
        open_only = (request.GET.get("open") or "").strip() == "1"

        valid_statuses = {c[0] for c in Application.Status.choices}
        if status and status in valid_statuses:
            qs = qs.filter(status=status)

        valid_types = {c[0] for c in Application.Type.choices}
        if applicant_type and applicant_type in valid_types:
            qs = qs.filter(applicant_type=applicant_type)

        if program_id.isdigit():
            qs = qs.filter(program_id=int(program_id))

        if open_only:
            today = timezone.now().date()
            qs = (
                qs.filter(program__active=True)
                .filter(
                    models.Q(program__applications_open__lte=today)
                    | models.Q(program__applications_open__isnull=True)
                )
                .filter(
                    models.Q(program__applications_close__gte=today)
                    | models.Q(program__applications_close__isnull=True)
                )
                .filter(
                    models.Q(program__end_date__gte=today)
                    | models.Q(program__end_date__isnull=True)
                )
            )

        # Sorting
        sort = (request.GET.get("sort") or "submitted").strip()
        direction = (request.GET.get("dir") or "desc").strip()

        sort_map = {
            "id": "application_id",
            "type": "applicant_type",
            "program": "program__name",
            "status": "status",
            "started": "created_at",
            "submitted": "submitted_at",
            "email": "email",
        }

        if sort in sort_map:
            order_by_val = sort_map[sort]
            if direction == "desc":
                if isinstance(order_by_val, str):
                    order_by_val = f"-{order_by_val}"
                else:
                    order_by_val = order_by_val.desc()
            qs = qs.order_by(order_by_val)

        # Grouping logic
        # Admin actions:
        # * Review to convert to student (all "App + Signed" statuses)
        # * Review to approve application (all "Submitted" statuses)
        # Applicant actions:
        # * Waiting on forms to be signed (all "App Approved" statuses)
        # * Waiting on Parent data (all "awaiting parent")
        # * Waiting on Student data (all "Email verified")
        # * No data yet (all "Draft" statuses)

        grouped = [
            {
                "title": "Admin actions: Review to convert to student",
                "statuses": [Application.Status.APPROVED_SIGNED],
                "apps": [],
            },
            {
                "title": "Admin actions: Review to approve application",
                "statuses": [Application.Status.SUBMITTED],
                "apps": [],
            },
            {
                "title": "Applicant actions: Waiting on forms to be signed",
                "statuses": [Application.Status.APPROVED],
                "apps": [],
            },
            {
                "title": "Applicant actions: Waiting on Parent data",
                "statuses": [Application.Status.AWAITING_PARENT],
                "apps": [],
            },
            {
                "title": "Applicant actions: Waiting on Student data",
                "statuses": [Application.Status.EMAIL_VERIFIED],
                "apps": [],
            },
            {
                "title": "Applicant actions: No data yet",
                "statuses": [Application.Status.DRAFT],
                "apps": [],
            },
            {
                "title": "Other (Converted / Declined)",
                "statuses": [Application.Status.CONVERTED, Application.Status.DECLINED],
                "apps": [],
            },
        ]

        invalid_group = {
            "title": "Closed or Ended Programs (Invalid)",
            "apps": [],
            "is_invalid": True,
        }

        # Partition applications into groups
        for app in qs:
            # Applications for closed/ended programs go into the special invalid group
            if app.program and app.program.applications_are_invalid:
                invalid_group["apps"].append(app)
                continue

            found = False
            for group in grouped:
                if app.status in group["statuses"]:
                    group["apps"].append(app)
                    found = True
                    break
            if not found:
                # Fallback group if we ever add a status forgot to categorize
                if not grouped or grouped[-1]["title"] != "Other":
                    grouped.append({"title": "Other", "statuses": [], "apps": []})
                grouped[-1]["apps"].append(app)

        # Add the invalid group at the end if it has apps
        if invalid_group["apps"]:
            grouped.append(invalid_group)

        # Remove empty groups if a filter is active
        if status or applicant_type or program_id or open_only:
            grouped = [g for g in grouped if g["apps"]]

        from programs.models import Program

        return render(
            request,
            self.template_name,
            {
                "grouped_applications": grouped,
                "status_choices": Application.Status.choices,
                "type_choices": Application.Type.choices,
                "programs": Program.objects.all().order_by("-active", "name"),
                "current_status": status,
                "current_type": applicant_type,
                "filter_program_id": program_id,
                "open_only": open_only,
                "current_sort": sort,
                "current_dir": direction,
            },
        )


class ApplicationReviewDetailView(_ReviewerRequiredMixin, View):
    """Detail page showing all collected data + action buttons."""

    template_name = "applications/review/detail.html"

    def get(self, request, app_id: str):
        application = get_object_or_404(
            Application, application_id=(app_id or "").upper()
        )
        # Build per-document status rows for the Signed Documents card.
        documents_status = []
        all_required_uploaded = True
        any_documents = False
        if application.program_id:
            from programs.models import ProgramDocument

            program_docs = ProgramDocument.objects.filter(
                program_id=application.program_id, is_active=True
            ).order_by("display_order", "name")
            submissions = {
                s.document_id: s
                for s in application.document_submissions.select_related("document")
            }
            for doc in program_docs:
                any_documents = True
                submission = submissions.get(doc.id)
                if doc.is_required and submission is None:
                    all_required_uploaded = False
                documents_status.append(
                    {
                        "document": doc,
                        "submission": submission,
                    }
                )
        # The Convert button is enabled when the application has been
        # approved and there are no required signed documents still
        # pending. APPROVED_SIGNED implies that's already true; APPROVED
        # with no required docs (or all required docs uploaded) is also
        # convertible — the auto-upgrade to APPROVED_SIGNED only fires
        # via the applicant's upload flow, so an applicant whose program
        # has no required documents would otherwise be stuck.
        can_convert = application.status == Application.Status.APPROVED_SIGNED or (
            application.status == Application.Status.APPROVED and all_required_uploaded
        )
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "data": application.data or {},
                "decline_form": DeclineForm(),
                "upload_form": StaffDocumentUploadForm(program=application.program),
                "documents_status": documents_status,
                "all_required_uploaded": all_required_uploaded,
                "any_documents": any_documents,
                "can_convert": can_convert,
            },
        )


class ApplicationApproveView(_ReviewerRequiredMixin, View):
    """POST: mark an application APPROVED and email the applicant."""

    def post(self, request, app_id: str):
        application = get_object_or_404(
            Application, application_id=(app_id or "").upper()
        )
        if application.status in (
            Application.Status.APPROVED,
            Application.Status.APPROVED_SIGNED,
            Application.Status.CONVERTED,
            Application.Status.DECLINED,
        ):
            if application.status == Application.Status.DECLINED:
                messages.info(request, "Cannot approve a declined application.")
            else:
                messages.info(request, "Application is already approved.")
            return redirect(
                "application_review_detail", app_id=application.application_id
            )

        old_status = application.status
        application.status = Application.Status.APPROVED
        application.reviewed_at = timezone.now()
        application.reviewed_by = request.user
        application.decline_reason = ""
        # Move them past the wizard's submit step; Step 9 will surface
        # the signed-document upload page next time they resume.
        application.current_step = max(application.current_step, 9)
        application.save()

        log_event(
            request=request,
            event=AuditEvent.ADMISSION_DECISION,
            resource=application,
            before={"status": old_status},
            after={"status": Application.Status.APPROVED},
            notes=f"Application approved by {request.user}.",
        )

        try:
            send_application_approved_email(application, request=request)
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "Failed to send approval email for %s",
                application.application_id,
            )
            messages.warning(
                request,
                "Application was approved, but the confirmation email "
                "could not be sent.",
            )
        else:
            messages.success(
                request,
                f"Approved application {application.application_id}. "
                "The applicant has been emailed.",
            )
        return redirect("application_review_detail", app_id=application.application_id)


class ApplicationDeclineView(_ReviewerRequiredMixin, View):
    """POST: mark an application DECLINED and email the applicant the reason."""

    template_name = "applications/review/decline.html"

    def _check_already_declined(self, request, application):
        """Return True if the application is already DECLINED/CONVERTED."""
        if application.status in (
            Application.Status.DECLINED,
            Application.Status.CONVERTED,
        ):
            messages.info(
                request,
                "This application has already been "
                + (
                    "converted."
                    if application.status == Application.Status.CONVERTED
                    else "declined."
                ),
            )
            return True
        return False

    def get(self, request, app_id: str):
        application = get_object_or_404(
            Application, application_id=(app_id or "").upper()
        )
        if self._check_already_declined(request, application):
            return redirect(
                "application_review_detail", app_id=application.application_id
            )
        return render(
            request,
            self.template_name,
            {"application": application, "form": DeclineForm()},
        )

    def post(self, request, app_id: str):
        application = get_object_or_404(
            Application, application_id=(app_id or "").upper()
        )
        if self._check_already_declined(request, application):
            return redirect(
                "application_review_detail", app_id=application.application_id
            )
        form = DeclineForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"application": application, "form": form},
            )
        reason = form.cleaned_data.get("reason") or ""
        old_status = application.status
        application.status = Application.Status.DECLINED
        application.decline_reason = reason
        application.reviewed_at = timezone.now()
        application.reviewed_by = request.user
        application.save()

        log_event(
            request=request,
            event=AuditEvent.ADMISSION_DECISION,
            resource=application,
            before={"status": old_status},
            after={"status": Application.Status.DECLINED},
            notes=f"Application declined by {request.user}. Reason: {reason}",
        )

        try:
            send_application_declined_email(application, reason=reason, request=request)
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "Failed to send decline email for %s",
                application.application_id,
            )
            messages.warning(
                request,
                "Application was declined, but the notification email "
                "could not be sent.",
            )
        else:
            messages.success(
                request,
                f"Declined application {application.application_id}. "
                "The applicant has been emailed.",
            )
        return redirect("application_review_detail", app_id=application.application_id)


class ApplicationEditView(_ReviewerRequiredMixin, View):
    """Per-field edit of the captured ``data`` and primary email."""

    template_name = "applications/review/edit.html"

    def _initial(self, application: Application):
        sections = _sections_for(application)
        initial = {"email": application.email}
        data = application.data or {}
        for data_key, _title, fields in sections:
            step = data.get(data_key) or {}
            for spec in fields:
                fname = spec["name"]
                field_name = ApplicationDataEditForm._field_name(data_key, fname)
                if fname in step:
                    value = step[fname]
                    if spec["kind"] == "date":
                        try:
                            value = datetime.date.fromisoformat(str(value))
                        except (TypeError, ValueError):
                            value = None
                    initial[field_name] = value
                elif spec.get("initial") is not None:
                    initial[field_name] = spec["initial"]
        return initial

    def _render(self, request, application, form):
        groups = []
        for data_key, title, fields in form.sections:
            bound = [
                form[ApplicationDataEditForm._field_name(data_key, spec["name"])]
                for spec in fields
            ]
            groups.append({"title": title, "fields": bound})
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": form,
                "groups": groups,
            },
        )

    def get(self, request, app_id: str):
        application = get_object_or_404(
            Application, application_id=(app_id or "").upper()
        )
        form = ApplicationDataEditForm(
            sections=_sections_for(application),
            initial=self._initial(application),
        )
        return self._render(request, application, form)

    def post(self, request, app_id: str):
        application = get_object_or_404(
            Application, application_id=(app_id or "").upper()
        )
        form = ApplicationDataEditForm(
            request.POST, sections=_sections_for(application)
        )
        if not form.is_valid():
            return self._render(request, application, form)
        old_data = application.data
        application.data = form.rebuild_data(application.data)
        new_email = (form.cleaned_data.get("email") or "").strip()
        if new_email:
            application.email = new_email
        application.save()

        log_event(
            request=request,
            event=AuditEvent.CONTACT_INFO_UPDATED,
            resource=application,
            before={
                "data": old_data,
                "email": application.email if not new_email else "changed",
            },
            after={"data": application.data, "email": application.email},
            notes=f"Application data edited by {request.user}.",
        )
        messages.success(
            request,
            f"Updated application {application.application_id}.",
        )
        return redirect("application_review_detail", app_id=application.application_id)


class ApplicationDeleteView(_ReviewerRequiredMixin, View):
    """Two-step delete: GET shows confirmation, POST performs the delete."""

    template_name = "applications/review/delete.html"

    def get(self, request, app_id: str):
        application = get_object_or_404(
            Application, application_id=(app_id or "").upper()
        )
        return render(request, self.template_name, {"application": application})

    def post(self, request, app_id: str):
        application = get_object_or_404(
            Application, application_id=(app_id or "").upper()
        )
        app_str = application.application_id
        application.delete()
        messages.success(request, f"Deleted application {app_str}.")
        return redirect("application_review_list")


class ApplicationCleanupView(_ReviewerRequiredMixin, View):
    """POST: Delete all applications older than 30 days."""

    def post(self, request):
        cutoff = timezone.now() - timezone.timedelta(days=30)
        stale_apps = Application.objects.filter(created_at__lt=cutoff)
        count = stale_apps.count()
        stale_apps.delete()

        if count > 0:
            messages.success(
                request, f"Deleted {count} applications older than 30 days."
            )
        else:
            messages.info(request, "No applications older than 30 days were found.")

        return redirect("application_review_list")


class ApplicationResendEmailView(_ReviewerRequiredMixin, View):
    """POST: resend various system emails (OTP, handoff, etc.)"""

    def post(self, request, app_id: str):
        application = get_object_or_404(
            Application, application_id=(app_id or "").upper()
        )
        email_type = request.POST.get("type")

        try:
            self._handle_resend(request, application, email_type)
        except Exception:
            logger.exception(
                "Failed to resend %s email for %s",
                email_type,
                application.application_id,
            )
            messages.error(
                request, f"Failed to resend {email_type} email. Please check logs."
            )

        return redirect("application_review_detail", app_id=application.application_id)

    def _handle_resend(self, request, application: Application, email_type: str):
        if email_type == "otp":
            code = application.issue_otp()
            send_otp_email(application, code, request=request)
            messages.success(
                request, f"Resent OTP verification email to {application.email}."
            )
        elif email_type == "handoff":
            parent_email = get_primary_parent_email(application)
            if not parent_email:
                messages.error(
                    request,
                    "No parent email address found. "
                    "This application might not have reached the handoff step yet.",
                )
            else:
                send_parent_handoff_email(application, parent_email, request=request)
                messages.success(
                    request, f"Resent parent handoff email to {parent_email}."
                )
        elif email_type == "submitted":
            if not application.submitted_at:
                messages.error(request, "This application has not been submitted yet.")
            else:
                send_application_submitted_email(application, request=request)
                messages.success(request, "Resent submission confirmation email.")
        elif email_type == "approved":
            if application.status not in (
                Application.Status.APPROVED,
                Application.Status.APPROVED_SIGNED,
                Application.Status.CONVERTED,
            ):
                messages.error(request, "This application has not been approved yet.")
            else:
                send_application_approved_email(application, request=request)
                messages.success(request, "Resent approval email.")
        elif email_type == "declined":
            if application.status != Application.Status.DECLINED:
                messages.error(request, "This application has not been declined yet.")
            else:
                send_application_declined_email(
                    application, application.decline_reason, request=request
                )
                messages.success(request, "Resent decline email.")
        elif email_type == "converted":
            if application.status != Application.Status.CONVERTED:
                messages.error(request, "This application has not been converted yet.")
            else:
                send_application_converted_email(application, request=request)
                messages.success(request, "Resent conversion enrollment email.")
        else:
            messages.error(request, f"Unknown email type: {email_type}")


class ApplicationConvertView(_ReviewerRequiredMixin, View):
    """POST: convert an APPROVED_SIGNED application into a real Student.

    Creates / updates Student + Adult records from the captured wizard
    data, enrolls the new (or existing) Student in the application's
    program, and flips the application status to CONVERTED.
    """

    def post(self, request, app_id: str):
        application = get_object_or_404(
            Application, application_id=(app_id or "").upper()
        )
        if application.status == Application.Status.CONVERTED:
            messages.info(
                request,
                "This application has already been converted to a student.",
            )
            return redirect(
                "application_review_detail", app_id=application.application_id
            )
        try:
            student = convert_application_to_student(application, request=request)
        except ApplicationConversionError as exc:
            messages.error(request, str(exc))
            return redirect(
                "application_review_detail", app_id=application.application_id
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "Failed to convert application %s", application.application_id
            )
            messages.error(
                request,
                "Something went wrong converting this application. "
                "Please check the data and try again.",
            )
            return redirect(
                "application_review_detail", app_id=application.application_id
            )
        if application.applicant_type == Application.Type.MENTOR:
            messages.success(
                request,
                f"Converted application {application.application_id} into "
                f"mentor “{student}”.",
            )
        else:
            messages.success(
                request,
                f"Converted application {application.application_id} into "
                f"student “{student}” enrolled in {application.program}.",
            )
        return redirect("application_review_detail", app_id=application.application_id)


class ApplicationEmailView(_ReviewerRequiredMixin, View):
    """Bulk email messaging for applicants by status and program."""

    template_name = "applications/review/email_form.html"

    def get(self, request):
        form = ApplicationEmailForm()
        return self._render(form)

    def post(self, request):
        form = ApplicationEmailForm(request.POST)
        if form.is_valid():
            prog = form.cleaned_data.get("program")
            statuses = form.cleaned_data["statuses"]
            subject = form.cleaned_data["subject"]
            html_body = form.cleaned_data["body"]

            # Inline CSS for better email client compatibility
            try:
                inlined_html_body = transform(html_body)
            except Exception:
                inlined_html_body = html_body
            text_body = strip_tags(inlined_html_body)
            test_email = form.cleaned_data.get("test_email")

            apps = Application.objects.filter(status__in=statuses)
            if prog:
                apps = apps.filter(program=prog)

            recipients = set()
            for app in apps:
                for addr in _collect_applicant_recipients(app):
                    recipients.add(addr)

            if not recipients and not test_email:
                messages.error(
                    request, "No recipients found for the selected criteria."
                )
                return self._render(form)

            to_send = [test_email] if test_email else sorted(recipients)

            # Determine sender account and SMTP credentials
            selected = form.cleaned_data.get("from_account")
            accounts = getattr(settings, "EMAIL_SENDER_ACCOUNTS", []) or []
            acc = None
            if accounts and selected and selected != "DEFAULT":
                # Match by key or email value
                for a in accounts:
                    key = a.get("key") or a.get("email")
                    if key == selected:
                        acc = a
                        break

            # Build SMTP connection using selected account credentials if provided
            conn_kwargs = {
                "backend": getattr(
                    settings,
                    "EMAIL_BACKEND",
                    "django.core.mail.backends.smtp.EmailBackend",
                ),
                "host": getattr(settings, "EMAIL_HOST", ""),
                "port": getattr(settings, "EMAIL_PORT", 465),
                "use_tls": getattr(settings, "EMAIL_USE_TLS", False),
                "use_ssl": getattr(settings, "EMAIL_USE_SSL", True),
                "timeout": getattr(settings, "EMAIL_TIMEOUT", 10),
            }
            if acc:
                conn_kwargs.update(
                    {
                        "username": acc.get("username") or "",
                        "password": acc.get("password") or "",
                    }
                )
                from_email = acc.get("email") or getattr(
                    settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"
                )
                # Include display_name if provided
                display_name = acc.get("display_name")
                if display_name:
                    from_email = f'"{display_name}" <{from_email}>'
            else:
                # Fall back to global credentials and default from address
                conn_kwargs.update(
                    {
                        "username": getattr(settings, "EMAIL_HOST_USER", ""),
                        "password": getattr(settings, "EMAIL_HOST_PASSWORD", ""),
                    }
                )
                from_email = getattr(
                    settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"
                )

            try:
                with get_connection(**conn_kwargs) as connection:
                    for addr in to_send:
                        msg = EmailMultiAlternatives(
                            subject=subject,
                            body=text_body,
                            from_email=from_email,
                            to=[addr],
                            connection=connection,
                        )
                        msg.attach_alternative(inlined_html_body, "text/html")
                        msg.send()

                messages.success(
                    request,
                    f"Successfully sent email to {len(to_send)} recipient(s).",
                )
                return redirect("application_review_list")
            except Exception as e:
                logger.exception("Failed to send bulk application email")
                messages.error(request, f"Failed to send email: {e}")

        return self._render(form)

    def _render(self, form):
        return render(self.request, self.template_name, {"form": form})


class ApplicationStaffDocumentUploadView(_ReviewerRequiredMixin, View):
    """POST: lead mentor uploads a signed document on behalf of an applicant.

    This allows paper copies received in person to be attached to the
    application so the reviewer can mark it as approved + signed.
    """

    http_method_names = ["post"]

    def post(self, request, app_id: str):
        from programs.models import ProgramDocument

        application = get_object_or_404(
            Application, application_id=(app_id or "").upper()
        )
        form = StaffDocumentUploadForm(
            request.POST, request.FILES, program=application.program
        )
        if not form.is_valid():
            messages.error(
                request,
                "Please select a document and choose a file to upload.",
            )
            return redirect(
                "application_review_detail", app_id=application.application_id
            )

        document = form.cleaned_data["document"]
        submission, _created = ApplicationDocumentSubmission.objects.get_or_create(
            application=application, document=document
        )
        submission.file = form.cleaned_data["file"]
        try:
            submission.save()
        except SuspiciousFileOperation:
            messages.error(
                request,
                "The filename of your uploaded document is too long or "
                "contains invalid characters. Please rename the file and "
                "try again.",
            )
            return redirect(
                "application_review_detail", app_id=application.application_id
            )

        # Auto-promote from APPROVED → APPROVED_SIGNED when all required
        # docs are now uploaded (mirrors the applicant's Step 10 logic).
        if application.status == Application.Status.APPROVED:
            required_doc_ids = set(
                ProgramDocument.objects.filter(
                    program=application.program,
                    is_active=True,
                    is_required=True,
                ).values_list("pk", flat=True)
            )
            if required_doc_ids:
                uploaded_doc_ids = set(
                    ApplicationDocumentSubmission.objects.filter(
                        application=application,
                        document_id__in=required_doc_ids,
                    ).values_list("document_id", flat=True)
                )
                if required_doc_ids.issubset(uploaded_doc_ids):
                    application.status = Application.Status.APPROVED_SIGNED
                    application.save(update_fields=["status", "updated_at"])

        messages.success(
            request,
            f'Uploaded signed copy of "{document.name}" for '
            f"{application.application_id}.",
        )
        return redirect("application_review_detail", app_id=application.application_id)
