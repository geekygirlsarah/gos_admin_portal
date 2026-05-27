"""Public application wizard views (Steps 1-8 + resume).

Step 9 (post-approval PDF download/upload) and the lead-mentor admin pages
are deferred to a later phase per the design spec.
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from programs.models import Student
from programs.utils import get_academic_year_ending

from .forms import (
    ApplicantTypeForm,
    ChooseExistingStudentForm,
    ConfirmSubmitForm,
    DocumentSubmissionForm,
    MentorClearanceDetailForm,
    MentorClearanceInterestForm,
    MentorInfoForm,
    OtpVerifyForm,
    ParentHandoffForm,
    ParentInfoForm,
    ProgramSelectForm,
    ResumeApplicationForm,
    StudentExperienceForm,
    StudentInfoForm,
)
from .models import Application, ApplicationDocumentSubmission, SiteSettings
from .services import (
    adult_to_prefill,
    find_adult_by_email,
    find_existing_mentor_by_email,
    find_student_by_email,
    get_program_buckets,
    get_student_emails,
    send_application_submitted_email,
    send_lead_notification_email,
    send_otp_email,
    send_parent_handoff_email,
    student_to_prefill,
    students_for_adult,
)

logger = logging.getLogger(__name__)

TOTAL_STEPS = 10  # Student/parent wizard step count.
MENTOR_TOTAL_STEPS = 6  # Mentor wizard step count.


def _is_mentor(application: Application) -> bool:
    return application.applicant_type == Application.Type.MENTOR


def _mentor_progress(view_key: str) -> tuple[int, int]:
    """Return (current_step, total_steps) for a mentor wizard page."""
    mapping = {
        "step2": 1,
        "step4": 2,
        "mentor_info": 3,
        "mentor_clearance_interest": 4,
        "mentor_clearance_detail": 5,
        "mentor_confirm": 6,
        "submitted": 6,
    }
    return mapping.get(view_key, 1), MENTOR_TOTAL_STEPS


def _get_application_or_404(app_id: str) -> Application:
    return get_object_or_404(Application, application_id=(app_id or "").upper())


def _redirect_to_current_step(application: Application):
    """After resume, send the user to the step they were last on."""
    # Mentor branch: no Step 3 program, no Steps 5/6/7. Map differently.
    if _is_mentor(application):
        step = application.current_step or 1
        if application.status in (
            Application.Status.SUBMITTED,
            Application.Status.APPROVED,
            Application.Status.APPROVED_SIGNED,
            Application.Status.CONVERTED,
        ):
            return redirect("apply_submitted", app_id=application.application_id)
        if step <= 2:
            return redirect("apply_step2", app_id=application.application_id)
        if not application.email_is_verified:
            return redirect("apply_step4", app_id=application.application_id)
        data = application.data or {}
        if not data.get("mentor_info"):
            return redirect("apply_mentor_info", app_id=application.application_id)
        if not data.get("mentor_clearance_interest"):
            return redirect(
                "apply_mentor_clearance_interest",
                app_id=application.application_id,
            )
        if data.get("mentor_clearance_interest", {}).get(
            "interested"
        ) == "yes" and not data.get("mentor_clearance_detail"):
            return redirect(
                "apply_mentor_clearance_detail",
                app_id=application.application_id,
            )
        return redirect("apply_mentor_confirm", app_id=application.application_id)

    step = max(1, min(application.current_step, TOTAL_STEPS))
    name_map = {
        2: "apply_step2",
        3: "apply_step3",
        4: "apply_step4",
        5: "apply_step5",
        6: "apply_step6",
        7: "apply_step7",
        8: "apply_step8",
        9: "apply_step9",
    }
    if step in name_map:
        return redirect(name_map[step], app_id=application.application_id)
    if step >= 10:
        # Approved applicants jump straight to the signed-documents page.
        # Everyone else lands on the post-submit confirmation page.
        if application.status in (
            Application.Status.APPROVED,
            Application.Status.APPROVED_SIGNED,
        ):
            return redirect("apply_step10", app_id=application.application_id)
        return redirect("apply_submitted", app_id=application.application_id)
    return redirect("apply_start")


# ---------------------------------------------------------------------------
# Step 1: welcome + resume
# ---------------------------------------------------------------------------


@method_decorator(never_cache, name="dispatch")
class WelcomeView(View):
    """Step 1: welcome page. Lets the user start a new application or resume one."""

    template_name = "applications/step1_welcome.html"

    def get(self, request):
        return render(
            request,
            self.template_name,
            {
                "settings_obj": SiteSettings.load(),
                "resume_form": ResumeApplicationForm(),
                "current_step": 1,
                "total_steps": TOTAL_STEPS,
                "application": None,
            },
        )

    def post(self, request):
        # POST on Step 1 means "start a new application".
        application = Application.objects.create()
        return redirect("apply_step2", app_id=application.application_id)


@method_decorator(never_cache, name="dispatch")
class ResumeView(View):
    """Handle the resume form on Step 1."""

    template_name = "applications/step1_welcome.html"

    def post(self, request):
        form = ResumeApplicationForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "settings_obj": SiteSettings.load(),
                    "resume_form": form,
                    "current_step": 1,
                    "total_steps": TOTAL_STEPS,
                    "resume_error": True,
                    "application": None,
                },
            )
        application = Application.objects.get(
            application_id=form.cleaned_data["application_id"]
        )
        messages.info(
            request,
            f"Welcome back! Resuming application {application.application_id}.",
        )
        return _redirect_to_current_step(application)


@method_decorator(never_cache, name="dispatch")
class ResumeLinkView(View):
    """Direct link from the welcome email — drop straight into the wizard."""

    def get(self, request, app_id: str, token: str = None):
        application = _get_application_or_404(app_id)
        if token and application.handoff_token and token == application.handoff_token:
            # Store the token in the session to authorize access to handed-off steps.
            request.session[f"handoff_token_{application.application_id}"] = token
        return _redirect_to_current_step(application)


# ---------------------------------------------------------------------------
# Step 2: applicant type + email
# ---------------------------------------------------------------------------


@method_decorator(never_cache, name="dispatch")
class Step2ApplicantTypeView(View):
    template_name = "applications/step2_applicant_type.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        form = ApplicantTypeForm(
            initial={
                "applicant_type": application.applicant_type or None,
                "email": application.email,
            }
        )
        return self._render(request, application, form)

    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        form = ApplicantTypeForm(request.POST)
        if not form.is_valid():
            return self._render(request, application, form)

        application.applicant_type = form.cleaned_data["applicant_type"]
        new_email = form.cleaned_data.get("email") or ""
        # Changing the email invalidates a prior verification.
        if new_email and new_email.lower() != (application.email or "").lower():
            application.email_verified_at = None
            application.otp_hash = ""
            application.otp_expires_at = None
            application.otp_attempts = 0
        application.email = new_email
        application.current_step = max(application.current_step, 3)
        application.save()

        # If a student didn't provide an email, tell them the parent should
        # take over and stop the wizard here for now.
        if (
            application.applicant_type == Application.Type.STUDENT
            and not application.email
        ):
            messages.info(
                request,
                "Since you don't have your own email yet, please ask a "
                "parent or guardian to fill out this application instead. "
                f"Your application ID is {application.application_id}.",
            )
            return redirect("apply_start")

        # Mentor applicants skip Step 4 (program selection) — they apply
        # to the organization, not to a specific program.
        if application.applicant_type == Application.Type.MENTOR:
            return redirect("apply_step3", app_id=application.application_id)
        return redirect("apply_step3", app_id=application.application_id)

    def _render(self, request, application, form):
        if _is_mentor(application):
            current_step, total_steps = _mentor_progress("step2")
        else:
            current_step, total_steps = 2, TOTAL_STEPS
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": form,
                "current_step": current_step,
                "total_steps": total_steps,
            },
        )


# ---------------------------------------------------------------------------
# Step 3: program selection
# ---------------------------------------------------------------------------


@method_decorator(never_cache, name="dispatch")
class Step4ProgramView(View):
    template_name = "applications/step4_program.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        future, current, past = get_program_buckets()
        form = ProgramSelectForm(
            future_programs=future,
            initial=(
                {"program": application.program_id} if application.program_id else None
            ),
        )
        return self._render(request, application, form, future, current, past)

    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        future, current, past = get_program_buckets()
        form = ProgramSelectForm(request.POST, future_programs=future)
        if not form.is_valid():
            return self._render(request, application, form, future, current, past)
        application.program = form.cleaned_data["program"]
        application.current_step = max(application.current_step, 5)
        application.save()
        return redirect("apply_continue", app_id=application.application_id)

    def _render(self, request, application, form, future, current, past):
        future_list = list(future)
        program_choices = list(zip(form["program"], future_list))
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": form,
                "future_programs": future_list,
                "program_choices": program_choices,
                "current_programs": current,
                "past_programs": past,
                "current_step": 4,
                "total_steps": TOTAL_STEPS,
            },
        )


# ---------------------------------------------------------------------------
# Step 4: email verification (OTP)
# ---------------------------------------------------------------------------


@method_decorator(never_cache, name="dispatch")
class Step3VerifyEmailView(View):
    template_name = "applications/step3_verify_email.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        if application.email_is_verified:
            return redirect("apply_step4", app_id=application.application_id)
        if not application.email:
            messages.error(
                request,
                "We need an email address before we can verify it. "
                "Please go back and fill in your email.",
            )
            return redirect("apply_step2", app_id=application.application_id)
        # Issue a fresh OTP whenever they land here without a pending one.
        if not application.otp_hash or not application.otp_expires_at:
            self._issue_and_send(application, request)
        return self._render(request, application, OtpVerifyForm())

    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        form = OtpVerifyForm(request.POST)
        if not form.is_valid():
            return self._render(request, application, form)
        if not application.verify_otp(form.cleaned_data["code"]):
            messages.error(
                request,
                "That code didn't match or has expired. "
                "Please check your email and try again, or request a new code.",
            )
            return self._render(request, application, form)
        application.current_step = max(application.current_step, 4)
        application.save(update_fields=["current_step", "updated_at"])
        messages.success(request, "Email verified — thanks!")
        # Mentor branch: block if the email already belongs to a mentor,
        # otherwise jump into the mentor info step.
        if _is_mentor(application):
            if find_existing_mentor_by_email(application.email):
                return redirect(
                    "apply_mentor_blocked", app_id=application.application_id
                )
            return redirect("apply_mentor_info", app_id=application.application_id)
        return redirect("apply_step4", app_id=application.application_id)

    def _issue_and_send(self, application: Application, request) -> None:
        try:
            code = application.issue_otp()
            send_otp_email(application, code, request=request)
        except Exception:
            logger.exception(
                "Failed to send OTP for application %s", application.application_id
            )

    def _render(self, request, application, form):
        if _is_mentor(application):
            current_step, total_steps = _mentor_progress("step4")
        else:
            current_step, total_steps = 3, TOTAL_STEPS
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": form,
                "current_step": current_step,
                "total_steps": total_steps,
            },
        )


@method_decorator(never_cache, name="dispatch")
class Step3ResendCodeView(View):
    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        if not application.email:
            return redirect("apply_step2", app_id=application.application_id)
        try:
            code = application.issue_otp()
            send_otp_email(application, code, request=request)
            messages.success(
                request, "A new verification code has been emailed to you."
            )
        except Exception:
            logger.exception(
                "Failed to resend OTP for application %s", application.application_id
            )
            messages.error(
                request,
                "Sorry, we couldn't send a new code right now. Please try again in a minute.",
            )
        return redirect("apply_step3", app_id=application.application_id)


# ---------------------------------------------------------------------------
# Helpers shared by Steps 5-8
# ---------------------------------------------------------------------------


def _require_verified_email(application: Application):
    if not application.email_is_verified:
        return redirect("apply_step3", app_id=application.application_id)
    return None


def _is_handoff_authorized(request, application: Application) -> bool:
    """Check if the current session is authorized to access handed-off steps.

    Required when a student-initiated application has been handed off to a
    parent (status=AWAITING_PARENT).
    """
    if application.status != Application.Status.AWAITING_PARENT:
        return True
    if application.applicant_type != Application.Type.STUDENT:
        return True

    expected = application.handoff_token
    if not expected:
        # If for some reason there is no token on file but it's awaiting
        # parent, we allow it (best effort for legacy/corrupt records).
        return True

    session_token = request.session.get(f"handoff_token_{application.application_id}")
    return bool(session_token and session_token == expected)


def _save_step_data(application: Application, key: str, payload: dict, next_step: int):
    """Persist a step's cleaned data into ``application.data`` and bump
    ``current_step`` if needed.
    """
    data = dict(application.data or {})
    data[key] = payload
    application.data = data
    application.current_step = max(application.current_step, next_step)
    application.save(update_fields=["data", "current_step", "updated_at"])


def _sanitize_payload(cleaned_data: dict) -> dict:
    """Prepare form.cleaned_data for storage in JSONField.
    - Dates/datetimes -> ISO string
    - QuerySets (from ModelChoice) -> list of PKs
    - Model instances (from ModelChoiceField) -> PK
    """
    payload = {}
    for k, v in cleaned_data.items():
        if hasattr(v, "isoformat"):
            payload[k] = v.isoformat()
        elif hasattr(v, "values_list"):
            # ModelMultipleChoiceField returns a QuerySet
            payload[k] = list(v.values_list("pk", flat=True))
        elif hasattr(v, "pk"):
            # ModelChoiceField returns a single model instance
            payload[k] = v.pk
        else:
            payload[k] = v
    return payload


# ---------------------------------------------------------------------------
# Continue: alias that drops the user back into the wizard at their current
# step. Kept so existing email links pointing at ``apply_continue`` still
# work after Phase 1.
# ---------------------------------------------------------------------------


@method_decorator(never_cache, name="dispatch")
class ContinueView(View):
    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        if not application.email_is_verified:
            return redirect("apply_step4", app_id=application.application_id)
        # Land them on the highest step they've reached so far (>= 5).
        application.current_step = max(application.current_step, 5)
        application.save(update_fields=["current_step", "updated_at"])
        return _redirect_to_current_step(application)


# ---------------------------------------------------------------------------
# Step 5: student information (with optional existing-student picker for
# parents who have multiple children on file).
# ---------------------------------------------------------------------------


def _student_initial_for(application: Application) -> dict:
    """Build the initial dict for the StudentInfoForm based on prior step
    data, then existing-record lookup, then bare email-only defaults."""
    saved = (application.data or {}).get("step5") or {}
    if saved:
        # If we have graduation_year, calculate grade back
        if "graduation_year" in saved and "grade" not in saved:
            academic_year_ending = get_academic_year_ending()
            grad_year = saved["graduation_year"]
            grade = 12 - (grad_year - academic_year_ending)
            if 1 <= grade <= 12:
                saved["grade"] = grade
        return saved
    # Try to prefill from an existing Student record.
    if application.applicant_type == Application.Type.STUDENT:
        existing = find_student_by_email(application.email)
        if existing:
            initial = student_to_prefill(existing)
            initial["personal_email"] = application.email
            return initial
        return {"personal_email": application.email}
    # Parent: prefill is decided by ChooseExistingStudent flow.
    return {}


@method_decorator(never_cache, name="dispatch")
class Step5StudentInfoView(View):
    template_name = "applications/step5_student_info.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_verified_email(application)
        if guard is not None:
            return guard

        program_start_date = (
            application.program.start_date if application.program else None
        )
        chosen_student, picker = self._existing_student_picker(application, request.GET)
        form = StudentInfoForm(
            initial=self._initial(application, chosen_student),
            program_start_date=program_start_date,
            tshirt_enabled=application.program.has_feature("tshirt-size")
            if application.program
            else False,
        )
        return self._render(
            request,
            application,
            form,
            picker,
            chosen_student,
            program_start_date=program_start_date,
        )

    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_verified_email(application)
        if guard is not None:
            return guard

        chosen_student, picker = self._existing_student_picker(
            application, request.POST
        )
        program_start_date = (
            application.program.start_date if application.program else None
        )
        # If the parent picked an existing student via the picker but
        # didn't yet submit the main form, just re-render with prefill.
        if picker is not None and "_pick_student" in request.POST:
            form = StudentInfoForm(
                initial=self._initial(application, chosen_student),
                program_start_date=program_start_date,
                tshirt_enabled=application.program.has_feature("tshirt-size")
                if application.program
                else False,
            )
            return self._render(
                request,
                application,
                form,
                picker,
                chosen_student,
                program_start_date=program_start_date,
            )

        form = StudentInfoForm(
            request.POST,
            program_start_date=program_start_date,
            tshirt_enabled=application.program.has_feature("tshirt-size")
            if application.program
            else False,
        )
        if not form.is_valid():
            return self._render(
                request,
                application,
                form,
                picker,
                chosen_student,
                program_start_date=program_start_date,
            )

        # Add warnings for age
        dob = form.cleaned_data["date_of_birth"]
        today = timezone.localdate()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

        warnings = []
        if age < 5:
            warnings.append(
                "The birthdate seems a bit young for our programs, please double-check the birthdate to continue."
            )
        elif age == 18:
            warnings.append(
                "The birthdate seems a bit old for our programs,  please double-check the birthdate to continue."
            )

        if warnings:
            # If we have warnings, the user MUST confirm the birthdate.
            form.fields["confirm_age"].required = True
            form._errors = None  # Force re-validation
            if not form.is_valid():
                return self._render(
                    request,
                    application,
                    form,
                    picker,
                    chosen_student,
                    warnings=warnings,
                )

        payload = _sanitize_payload(form.cleaned_data)
        # Convert grade to graduation_year if not already present
        if "grade" in payload:
            grade = int(payload["grade"])
            academic_year_ending = get_academic_year_ending()
            # If graduation_year not provided by the form, calculate it from grade
            if not payload.get("graduation_year"):
                payload["graduation_year"] = academic_year_ending + (12 - grade)

        if chosen_student is not None:
            payload["_existing_student_id"] = chosen_student.pk
        elif application.applicant_type == Application.Type.STUDENT:
            # Returning student: remember which Student record matched so
            # Step 6 can prefill the primary parent/guardian automatically.
            matched = find_student_by_email(application.email)
            if matched is not None:
                payload["_existing_student_id"] = matched.pk
        _save_step_data(application, "step5", payload, next_step=6)

        return redirect("apply_step6", app_id=application.application_id)

    def _existing_student_picker(self, application, post=None):
        """Return (chosen_student, picker_form_or_None)."""
        if application.applicant_type != Application.Type.PARENT:
            return None, None
        adult = find_adult_by_email(application.email)
        students = students_for_adult(adult)
        if not students:
            return None, None
        qs = Student.objects.filter(pk__in=[s.pk for s in students])
        picker = ChooseExistingStudentForm(post or None, students=qs)
        chosen = None
        # Honor previously-saved choice in application.data
        prior_id = (application.data or {}).get("step5", {}).get("_existing_student_id")
        if post is not None and picker.is_valid():
            chosen = picker.cleaned_data.get("student")
        elif prior_id:
            chosen = qs.filter(pk=prior_id).first()
        return chosen, picker

    def _initial(self, application, chosen_student):
        if chosen_student is not None:
            initial = student_to_prefill(chosen_student)
            initial["personal_email"] = (
                chosen_student.personal_email or application.email or ""
            )
            # Add grade prefill
            if chosen_student.graduation_year:
                academic_year_ending = get_academic_year_ending()
                # Grade calculation: 12 - (grad_year - academic_year_ending)
                grade = 12 - (chosen_student.graduation_year - academic_year_ending)
                if 1 <= grade <= 12:
                    initial["grade"] = grade
            return initial
        return _student_initial_for(application)

    def _render(
        self,
        request,
        application,
        form,
        picker,
        chosen_student,
        warnings=None,
        program_start_date=None,
    ):
        from .services import latest_program_for_student

        academic_year_ending = get_academic_year_ending()

        # Determine which Student record (if any) we're prefilling from so
        # we can show a friendly "Welcome back" banner.
        prefill_student = chosen_student
        if (
            prefill_student is None
            and application.applicant_type == Application.Type.STUDENT
            and not (application.data or {}).get("step5")
        ):
            prefill_student = find_student_by_email(application.email)
        welcome_back = None
        if prefill_student is not None:
            display_name = (
                prefill_student.first_name
                or prefill_student.legal_first_name
                or str(prefill_student)
            )
            welcome_back = {
                "name": display_name,
                "last_program": latest_program_for_student(prefill_student),
            }

        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": form,
                "picker": picker,
                "chosen_student": chosen_student,
                "warnings": warnings,
                "welcome_back": welcome_back,
                "academic_year_ending": academic_year_ending,
                "program_start_date": program_start_date,
                "current_step": 5,
                "total_steps": TOTAL_STEPS,
            },
        )


@method_decorator(never_cache, name="dispatch")
class Step6ExperienceView(View):
    template_name = "applications/step6_experience.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_verified_email(application)
        if guard is not None:
            return guard

        initial = (application.data or {}).get("step6") or {}
        form = StudentExperienceForm(
            initial=initial, applicant_type=application.applicant_type
        )
        return self._render(request, application, form)

    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_verified_email(application)
        if guard is not None:
            return guard

        form = StudentExperienceForm(
            request.POST, applicant_type=application.applicant_type
        )
        if not form.is_valid():
            return self._render(request, application, form)

        payload = _sanitize_payload(form.cleaned_data)
        _save_step_data(application, "step6", payload, next_step=7)
        return redirect("apply_step7", app_id=application.application_id)

    def _render(self, request, application, form):
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": form,
                "current_step": 6,
                "total_steps": TOTAL_STEPS,
            },
        )


# ---------------------------------------------------------------------------
# Step 6: primary parent / guardian (with optional handoff if a student
# started the application).
# ---------------------------------------------------------------------------


@method_decorator(never_cache, name="dispatch")
class Step7PrimaryParentView(View):
    template_name = "applications/step7_primary_parent.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_verified_email(application)
        if guard is not None:
            return guard

        if not _is_handoff_authorized(request, application):
            return render(
                request,
                "applications/handoff_required.html",
                {"application": application},
            )

        form, handoff_form, mode, existing_adult = self._build_forms(application)
        return self._render(
            request, application, form, handoff_form, mode, existing_adult
        )

    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_verified_email(application)
        if guard is not None:
            return guard

        if not _is_handoff_authorized(request, application):
            return render(
                request,
                "applications/handoff_required.html",
                {"application": application},
            )

        form, handoff_form, mode, existing_adult = self._build_forms(
            application, request.POST
        )

        if mode == "handoff":
            if not handoff_form.is_valid():
                return self._render(
                    request, application, form, handoff_form, mode, existing_adult
                )
            parent_email = handoff_form.cleaned_data["parent_email"]
            data = dict(application.data or {})
            data["step7_handoff"] = {"parent_email": parent_email}
            application.data = data
            application.status = Application.Status.AWAITING_PARENT
            application.current_step = max(application.current_step, 7)
            application.issue_handoff_token()
            application.save(
                update_fields=["data", "status", "current_step", "updated_at"]
            )
            send_parent_handoff_email(application, parent_email, request=request)
            messages.success(
                request,
                "We emailed the parent/guardian a link to continue this "
                "application. You can close this window — they'll take "
                f"over from here. Application ID: {application.application_id}.",
            )
            return redirect("apply_start")

        # Normal form submission for primary parent info.
        if not form.is_valid():
            return self._render(
                request, application, form, handoff_form, mode, existing_adult
            )
        payload = _sanitize_payload(form.cleaned_data)
        _save_step_data(application, "step7", payload, next_step=8)
        return redirect("apply_step8", app_id=application.application_id)

    def _build_forms(self, application, post=None):
        """Decide which form mode to show:
        - "handoff": student initiated and no parent email is on file yet,
          ask for a parent email to take over.
        - "form": show the actual ParentInfoForm.
        Returns (form, handoff_form, mode).
        """
        saved = (application.data or {}).get("step7") or {}
        # If the student initiated and we don't have a parent yet, default to
        # asking for a handoff email — unless they're already filling out the
        # form (post with parent fields), or they previously entered parent
        # info.
        student_initiated = application.applicant_type == Application.Type.STUDENT
        already_handed_off = bool((application.data or {}).get("step7_handoff"))
        # If a handoff already happened, the parent has now arrived via the
        # email link. Look the adult up by the parent_email captured at
        # handoff time (rather than the student's email) and show them the
        # parent form directly — never loop back into handoff mode.
        handoff_email = (
            (application.data or {}).get("step7_handoff", {}).get("parent_email")
        )
        existing_adult = None
        # If Step 5 linked us to an existing Student record (returning
        # student, or parent who picked one of their children), prefer that
        # student's primary_contact Adult for prefill so the applicant
        # doesn't have to retype it.
        existing_student_id = (
            (application.data or {}).get("step5", {}).get("_existing_student_id")
        )
        if not saved and existing_student_id:
            student = Student.objects.filter(pk=existing_student_id).first()
            if student and student.primary_contact_id:
                existing_adult = student.primary_contact
        if not saved and existing_adult is None:
            lookup_email = handoff_email or application.email
            existing_adult = find_adult_by_email(lookup_email)

        student_emails = get_student_emails(application)
        if student_initiated and not saved and not already_handed_off:
            # Student is applying — the parent still needs to take over.
            # If we already know the parent's email (returning student's
            # primary_contact, or a found Adult), prefill the handoff form
            # so the student doesn't have to look it up; they just confirm.
            mode = "handoff"
            handoff_initial = {}
            if existing_adult and existing_adult.email:
                handoff_initial["parent_email"] = existing_adult.email
            if post:
                handoff_form = ParentHandoffForm(post, student_emails=student_emails)
            else:
                handoff_form = ParentHandoffForm(
                    initial=handoff_initial, student_emails=student_emails
                )
            return (
                ParentInfoForm(initial=saved or {}, student_emails=student_emails),
                handoff_form,
                mode,
                existing_adult,
            )

        # Prefer prior saved data, then existing adult lookup. If we came in
        # via a handoff and have no other info, at least seed the parent's
        # email field.
        initial = saved or (adult_to_prefill(existing_adult) if existing_adult else {})
        if not initial:
            if handoff_email:
                initial = {"email": handoff_email}
            elif application.applicant_type == Application.Type.PARENT:
                initial = {"email": application.email}
        # For new applications (no saved data), default primary to opted-in.
        if not saved and "email_updates" not in initial:
            initial["email_updates"] = True
        form = ParentInfoForm(
            post or None, initial=initial, student_emails=student_emails
        )
        return (
            form,
            ParentHandoffForm(student_emails=student_emails),
            "form",
            existing_adult,
        )

    def _render(
        self, request, application, form, handoff_form, mode, existing_adult=None
    ):
        from .services import latest_program_for_adult

        welcome_back = None
        # Only show "welcome back" if we actually prefilled from an existing
        # adult record (i.e., we're in form mode and the form is unbound, or
        # bound but came in with prefilled initial data). Don't show it if
        # the applicant has already saved step7 data in a previous visit.
        saved = (application.data or {}).get("step7") or {}
        if existing_adult is not None and not saved and mode == "form":
            display_name = existing_adult.first_name or str(existing_adult)
            welcome_back = {
                "name": display_name,
                "last_program": latest_program_for_adult(existing_adult),
            }
        # Show the swap box if step8 is already saved, OR if the returning
        # student has a secondary_contact on file (step8 prefill exists but
        # hasn't been submitted yet).  Also collect the secondary's display
        # name so the template can show it in the swap prompt.
        has_secondary = False
        secondary_name = None
        step8_data = (application.data or {}).get("step8") or {}
        if step8_data:
            has_secondary = True
            first = step8_data.get("first_name") or ""
            last = step8_data.get("last_name") or ""
            secondary_name = " ".join(filter(None, [first, last])) or None
        if not has_secondary:
            sid = (application.data or {}).get("step5", {}).get("_existing_student_id")
            if sid:
                student = Student.objects.filter(pk=sid).first()
                if student and student.secondary_contact_id:
                    has_secondary = True
                    sc = student.secondary_contact
                    secondary_name = (
                        " ".join(
                            filter(None, [sc.first_name or "", sc.last_name or ""])
                        )
                        or None
                    )
        # Also check via the existing_adult (or a fresh email lookup when
        # step6 was already saved and existing_adult wasn't populated):
        # if this adult is the primary contact for any student that has a
        # secondary_contact, show the swap box.
        if not has_secondary:
            adult_for_check = existing_adult
            if adult_for_check is None:
                adult_for_check = find_adult_by_email(application.email)
            if adult_for_check is not None:
                linked_student = Student.objects.filter(
                    primary_contact=adult_for_check,
                    secondary_contact__isnull=False,
                ).first()
                if linked_student:
                    has_secondary = True
                    sc = linked_student.secondary_contact
                    secondary_name = (
                        " ".join(
                            filter(None, [sc.first_name or "", sc.last_name or ""])
                        )
                        or None
                    )

        step5_data = (application.data or {}).get("step5") or {}
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": form,
                "handoff_form": handoff_form,
                "mode": mode,
                "welcome_back": welcome_back,
                "has_secondary": has_secondary,
                "secondary_name": secondary_name,
                "student_address": step5_data.get("address"),
                "student_city": step5_data.get("city"),
                "student_state": step5_data.get("state"),
                "student_zip_code": step5_data.get("zip_code"),
                "current_step": 7,
                "total_steps": TOTAL_STEPS,
            },
        )


# ---------------------------------------------------------------------------
# Swap primary / secondary parent data (POST only).
# ---------------------------------------------------------------------------


@method_decorator(never_cache, name="dispatch")
class SwapParentsView(View):
    """Swap the saved step7 (primary) and step8 (secondary) parent data.

    Only meaningful when both steps have already been filled in (e.g. a
    returning student whose contacts are pre-filled).  After the swap the
    user is redirected back to whichever step they came from (``next``
    query-param, defaulting to step 7).
    """

    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_verified_email(application)
        if guard is not None:
            return guard

        if not _is_handoff_authorized(request, application):
            return render(
                request,
                "applications/handoff_required.html",
                {"application": application},
            )

        data = dict(application.data or {})
        step7 = data.get("step7") or {}
        step8 = data.get("step8") or {}

        # For returning students the wizard prefills from the Student record
        # but doesn't save to application.data until the user submits each
        # step.  If either side is empty, hydrate it from the Student record
        # so the swap has something to work with.
        existing_student_id = (data.get("step5") or {}).get("_existing_student_id")
        if existing_student_id and (not step7 or not step8):
            student = Student.objects.filter(pk=existing_student_id).first()
            if student:
                if not step7 and student.primary_contact_id:
                    step7 = adult_to_prefill(student.primary_contact)
                if not step8 and student.secondary_contact_id:
                    step8 = adult_to_prefill(student.secondary_contact)

        # Only swap when both sides have content.
        if step7 or step8:
            data["step7"], data["step8"] = step8, step7
            application.data = data
            application.save(update_fields=["data", "updated_at"])
            messages.success(
                request,
                "Primary and secondary parent / guardian information has been swapped.",
            )

        next_step = request.POST.get("next", "7")
        if next_step == "8":
            return redirect("apply_step8", app_id=application.application_id)
        return redirect("apply_step7", app_id=application.application_id)


# ---------------------------------------------------------------------------
# Step 7: secondary parent / guardian (required, no email verification).
# ---------------------------------------------------------------------------


@method_decorator(never_cache, name="dispatch")
class Step8SecondaryParentView(View):
    template_name = "applications/step8_secondary_parent.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_verified_email(application)
        if guard is not None:
            return guard

        if not _is_handoff_authorized(request, application):
            return render(
                request,
                "applications/handoff_required.html",
                {"application": application},
            )

        student_emails = get_student_emails(application)
        form = ParentInfoForm(
            initial=self._initial(application),
            require_email=False,
            require_address=False,
            student_emails=student_emails,
        )
        return self._render(request, application, form)

    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_verified_email(application)
        if guard is not None:
            return guard

        if not _is_handoff_authorized(request, application):
            return render(
                request,
                "applications/handoff_required.html",
                {"application": application},
            )

        student_emails = get_student_emails(application)
        form = ParentInfoForm(
            request.POST,
            require_email=False,
            require_address=False,
            student_emails=student_emails,
        )
        if not form.is_valid():
            return self._render(request, application, form)

        # If the secondary parent is also opting out, check whether the primary
        # parent opted in. If neither opted in, surface the error here (step 8)
        # rather than making the applicant go all the way to step 9 to find out.
        # Only run this check when step 7 data is present (primary parent was collected).
        step7_data = (application.data or {}).get("step7") or {}
        p1_opt = step7_data.get("email_updates", False)
        p2_opt = form.cleaned_data.get("email_updates", False)
        if step7_data and not p1_opt and not p2_opt:
            form.add_error(
                None,
                "At least one adult contact must opt in to receiving email "
                "updates from Girls of Steel. Please check the box on this page or "
                "go back and check it on the primary contact page.",
            )
            return self._render(request, application, form)

        payload = _sanitize_payload(form.cleaned_data)
        _save_step_data(application, "step8", payload, next_step=9)
        return redirect("apply_step9", app_id=application.application_id)

    def _initial(self, application):
        saved = (application.data or {}).get("step8") or {}

        # If we have actual form data, return it regardless of _skipped
        data = {k: v for k, v in saved.items() if not k.startswith("_")}
        if data:
            return data

        # If we only have underscore-prefixed keys (like _skipped),
        # and _skipped is true, don't prefill.
        if saved.get("_skipped"):
            return {}

        # Prefill from existing secondary contact if we can identify the student.
        sid = (application.data or {}).get("step5", {}).get("_existing_student_id")
        if sid:
            student = Student.objects.filter(pk=sid).first()
            if student and student.secondary_contact_id:
                return adult_to_prefill(student.secondary_contact)
        return {}

    def _render(self, request, application, form):
        data = application.data or {}
        step5_data = data.get("step5") or {}
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": form,
                "student_address": step5_data.get("address"),
                "student_city": step5_data.get("city"),
                "student_state": step5_data.get("state"),
                "student_zip_code": step5_data.get("zip_code"),
                "current_step": 8,
                "total_steps": TOTAL_STEPS,
            },
        )


# ---------------------------------------------------------------------------
# Step 8: final confirmation + submission.
# ---------------------------------------------------------------------------


@method_decorator(never_cache, name="dispatch")
class Step9ConfirmView(View):
    template_name = "applications/step9_confirm.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_verified_email(application)
        if guard is not None:
            return guard

        if not _is_handoff_authorized(request, application):
            return render(
                request,
                "applications/handoff_required.html",
                {"application": application},
            )

        return self._render(request, application, ConfirmSubmitForm())

    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_verified_email(application)
        if guard is not None:
            return guard

        if not _is_handoff_authorized(request, application):
            return render(
                request,
                "applications/handoff_required.html",
                {"application": application},
            )

        form = ConfirmSubmitForm(request.POST)
        if not form.is_valid():
            return self._render(request, application, form)

        # Ensure at least one parent opted in to notifications (only for students/parents)
        data = application.data or {}
        p1_opt = (data.get("step7") or {}).get("email_updates", False)
        p2_opt = (data.get("step8") or {}).get("email_updates", False)
        if not p1_opt and not p2_opt:
            form.add_error(
                None,
                "At least one parent or guardian must opt in to receiving email "
                "updates from Girls of Steel.",
            )
            return self._render(request, application, form)

        application.status = Application.Status.SUBMITTED
        application.submitted_at = timezone.now()
        application.current_step = 10
        application.save(
            update_fields=["status", "submitted_at", "current_step", "updated_at"]
        )

        try:
            send_application_submitted_email(application, request=request)
        except Exception:
            logger.exception(
                "Failed to send submitted email for %s",
                application.application_id,
            )
        try:
            send_lead_notification_email(application, request=request)
        except Exception:
            logger.exception(
                "Failed to send lead notification for %s",
                application.application_id,
            )

        return redirect("apply_submitted", app_id=application.application_id)

    def _render(self, request, application, form):
        data = application.data or {}
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": form,
                "step5_data": data.get("step5") or {},
                "step6_data": data.get("step6") or {},
                "step7_data": data.get("step7") or {},
                "step8_data": data.get("step8") or {},
                "step8_skipped": bool((data.get("step8") or {}).get("_skipped")),
                "current_step": 9,
                "total_steps": TOTAL_STEPS,
            },
        )


@method_decorator(never_cache, name="dispatch")
class SubmittedView(View):
    template_name = "applications/submitted.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        # Approved student/parent applicants belong on the documents page.
        # Mentors have no Step 10 documents flow.
        if not _is_mentor(application) and application.status in (
            Application.Status.APPROVED,
            Application.Status.APPROVED_SIGNED,
        ):
            return redirect("apply_step10", app_id=application.application_id)
        if _is_mentor(application):
            current_step, total_steps = _mentor_progress("submitted")
        else:
            current_step = min(max(application.current_step, 10), TOTAL_STEPS)
            total_steps = TOTAL_STEPS
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "current_step": current_step,
                "total_steps": total_steps,
                "step5_data": application.data.get("step5") or {},
                "step6_data": application.data.get("step6") or {},
                "step7_data": application.data.get("step7") or {},
                "step8_data": application.data.get("step8") or {},
                "step8_skipped": bool(
                    (application.data.get("step8") or {}).get("_skipped")
                ),
                "mentor_info_data": application.data.get("mentor_info") or {},
            },
        )


# ---------------------------------------------------------------------------
# Step 9: post-approval signed-document download + upload.
#
# This page is only reachable once a lead mentor has approved the
# application (``application.status == APPROVED``). It lists every active
# :class:`programs.ProgramDocument` attached to the application's
# program, shows the applicant any submissions they've already uploaded,
# and lets them upload (or replace) a signed copy per document.
# ---------------------------------------------------------------------------


@method_decorator(never_cache, name="dispatch")
class Step10DocumentsView(View):
    template_name = "applications/step10_documents.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = self._gate(application)
        if guard is not None:
            return guard
        return self._render(request, application, DocumentSubmissionForm())

    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = self._gate(application)
        if guard is not None:
            return guard

        # Identify which ProgramDocument this upload is for. Must belong to
        # the application's program and be active.
        from programs.models import ProgramDocument

        doc_id = request.POST.get("document_id")
        document = (
            ProgramDocument.objects.filter(
                pk=doc_id,
                program=application.program,
                is_active=True,
            ).first()
            if doc_id
            else None
        )
        if document is None:
            messages.error(request, "We couldn't find that document. Please try again.")
            return redirect("apply_step10", app_id=application.application_id)

        form = DocumentSubmissionForm(request.POST, request.FILES)
        if not form.is_valid():
            return self._render(request, application, form, focus_doc_id=document.pk)

        submission, _created = ApplicationDocumentSubmission.objects.get_or_create(
            application=application, document=document
        )
        submission.file = form.cleaned_data["file"]
        submission.save()

        # If every required ProgramDocument now has a submission, promote
        # the application from APPROVED -> APPROVED_SIGNED so lead mentors
        # can see at a glance that the paperwork is in.
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
            f"Uploaded signed copy of “{document.name}”. Thank you!",
        )
        return redirect("apply_step10", app_id=application.application_id)

    def _gate(self, application: Application):
        """Only approved applications may access Step 9."""
        if application.status not in (
            Application.Status.APPROVED,
            Application.Status.APPROVED_SIGNED,
        ):
            # Send them somewhere sensible: their current step, or the
            # post-submit confirmation page if they've already submitted.
            return _redirect_to_current_step(application)
        return None

    def _render(self, request, application, form, focus_doc_id=None):
        from programs.models import ProgramDocument

        documents = list(
            ProgramDocument.objects.filter(
                program=application.program, is_active=True
            ).order_by("display_order", "name")
        )
        submissions_by_doc = {
            s.document_id: s
            for s in ApplicationDocumentSubmission.objects.filter(
                application=application
            ).select_related("document")
        }
        rows = []
        all_required_done = True
        for doc in documents:
            submission = submissions_by_doc.get(doc.pk)
            if doc.is_required and submission is None:
                all_required_done = False
            rows.append({"document": doc, "submission": submission})
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": form,
                "rows": rows,
                "all_required_done": all_required_done,
                "focus_doc_id": focus_doc_id,
                "current_step": 10,
                "total_steps": TOTAL_STEPS,
            },
        )


# ---------------------------------------------------------------------------
# Mentor wizard branch
# ---------------------------------------------------------------------------


def _require_mentor(application: Application):
    """Redirect non-mentor applicants back to their own flow."""
    if not _is_mentor(application):
        return _redirect_to_current_step(application)
    return None


def _require_mentor_verified(application: Application):
    """Mentor pages past OTP require a verified email + mentor type."""
    guard = _require_mentor(application)
    if guard is not None:
        return guard
    if not application.email_is_verified:
        return redirect("apply_step3", app_id=application.application_id)
    # Block existing-mentor applicants from continuing past OTP.
    if find_existing_mentor_by_email(application.email):
        return redirect("apply_mentor_blocked", app_id=application.application_id)
    return None


@method_decorator(never_cache, name="dispatch")
class MentorBlockedView(View):
    """Shown when the OTP-verified email already belongs to a mentor on file."""

    template_name = "applications/mentor_blocked.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        if not _is_mentor(application):
            return _redirect_to_current_step(application)
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "current_step": _mentor_progress("step4")[0],
                "total_steps": MENTOR_TOTAL_STEPS,
            },
        )


@method_decorator(never_cache, name="dispatch")
class MentorInfoView(View):
    template_name = "applications/mentor_info.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_mentor_verified(application)
        if guard is not None:
            return guard
        initial = (application.data or {}).get("mentor_info") or {}
        return self._render(request, application, MentorInfoForm(initial=initial))

    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_mentor_verified(application)
        if guard is not None:
            return guard
        form = MentorInfoForm(request.POST)
        if not form.is_valid():
            return self._render(request, application, form)
        _save_step_data(
            application, "mentor_info", dict(form.cleaned_data), next_step=6
        )
        return redirect(
            "apply_mentor_clearance_interest", app_id=application.application_id
        )

    def _render(self, request, application, form):
        current_step, total_steps = _mentor_progress("mentor_info")
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": form,
                "current_step": current_step,
                "total_steps": total_steps,
            },
        )


@method_decorator(never_cache, name="dispatch")
class MentorClearanceInterestView(View):
    template_name = "applications/mentor_clearance_interest.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_mentor_verified(application)
        if guard is not None:
            return guard
        initial = (application.data or {}).get("mentor_clearance_interest") or {}
        return self._render(
            request, application, MentorClearanceInterestForm(initial=initial)
        )

    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_mentor_verified(application)
        if guard is not None:
            return guard
        form = MentorClearanceInterestForm(request.POST)
        if not form.is_valid():
            return self._render(request, application, form)
        _save_step_data(
            application,
            "mentor_clearance_interest",
            dict(form.cleaned_data),
            next_step=7,
        )
        if form.cleaned_data["interested"] == "yes":
            return redirect(
                "apply_mentor_clearance_detail",
                app_id=application.application_id,
            )
        # Not interested → clear any prior detail and jump to confirm.
        data = dict(application.data or {})
        data.pop("mentor_clearance_detail", None)
        application.data = data
        application.save(update_fields=["data", "updated_at"])
        return redirect("apply_mentor_confirm", app_id=application.application_id)

    def _render(self, request, application, form):
        current_step, total_steps = _mentor_progress("mentor_clearance_interest")
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": form,
                "current_step": current_step,
                "total_steps": total_steps,
            },
        )


@method_decorator(never_cache, name="dispatch")
class MentorClearanceDetailView(View):
    template_name = "applications/mentor_clearance_detail.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_mentor_verified(application)
        if guard is not None:
            return guard
        # Don't show this page if they said "no" on the interest step.
        interest = (application.data or {}).get("mentor_clearance_interest") or {}
        if interest.get("interested") != "yes":
            return redirect(
                "apply_mentor_clearance_interest",
                app_id=application.application_id,
            )
        initial = (application.data or {}).get("mentor_clearance_detail") or {}
        return self._render(
            request, application, MentorClearanceDetailForm(initial=initial)
        )

    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_mentor_verified(application)
        if guard is not None:
            return guard
        form = MentorClearanceDetailForm(request.POST)
        if not form.is_valid():
            return self._render(request, application, form)
        _save_step_data(
            application,
            "mentor_clearance_detail",
            dict(form.cleaned_data),
            next_step=8,
        )
        return redirect("apply_mentor_confirm", app_id=application.application_id)

    def _render(self, request, application, form):
        current_step, total_steps = _mentor_progress("mentor_clearance_detail")
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": form,
                "current_step": current_step,
                "total_steps": total_steps,
            },
        )


@method_decorator(never_cache, name="dispatch")
class MentorConfirmView(View):
    template_name = "applications/mentor_confirm.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_mentor_verified(application)
        if guard is not None:
            return guard
        return self._render(request, application, ConfirmSubmitForm())

    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_mentor_verified(application)
        if guard is not None:
            return guard
        form = ConfirmSubmitForm(request.POST)
        if not form.is_valid():
            return self._render(request, application, form)
        application.status = Application.Status.SUBMITTED
        application.submitted_at = timezone.now()
        application.current_step = max(application.current_step, 9)
        application.save(
            update_fields=["status", "submitted_at", "current_step", "updated_at"]
        )
        try:
            send_application_submitted_email(application, request=request)
        except Exception:
            logger.exception(
                "Failed to send mentor submitted email for %s",
                application.application_id,
            )
        try:
            send_lead_notification_email(application, request=request)
        except Exception:
            logger.exception(
                "Failed to send lead notification for mentor %s",
                application.application_id,
            )
        return redirect("apply_submitted", app_id=application.application_id)

    def _render(self, request, application, form):
        data = application.data or {}
        current_step, total_steps = _mentor_progress("mentor_confirm")
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": form,
                "mentor_info": data.get("mentor_info") or {},
                "clearance_interest": data.get("mentor_clearance_interest") or {},
                "clearance_detail": data.get("mentor_clearance_detail") or {},
                "current_step": current_step,
                "total_steps": total_steps,
            },
        )
