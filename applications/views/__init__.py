"""Application wizard views — re-exports for backward compatibility.

This package re-exports all view classes and helper functions so that
existing imports (``from applications.views import ...``) continue to work
after the single-file ``views.py`` was split into submodules.
"""

from __future__ import annotations

from .documents import Step10DocumentsView
from .review import (
    REVIEW_PERM,
    ApplicationApproveView,
    ApplicationCleanupView,
    ApplicationConvertView,
    ApplicationDataEditForm,
    ApplicationDeclineView,
    ApplicationDeleteView,
    ApplicationEditView,
    ApplicationEmailView,
    ApplicationResendEmailView,
    ApplicationReviewDetailView,
    ApplicationReviewListView,
    DeclineForm,
    _ReviewerRequiredMixin,
)
from .steps_mentor import (
    MentorBlockedView,
    MentorClearanceDetailView,
    MentorClearanceInterestView,
    MentorConfirmView,
    MentorInfoView,
)
from .steps_student import (
    Step2ApplicantTypeView,
    Step3DuplicateFoundView,
    Step3ResendCodeView,
    Step3VerifyEmailView,
    Step4ProgramView,
    Step5StudentInfoView,
    Step6ExperienceView,
    Step7PrimaryParentView,
    Step8SecondaryParentView,
    Step9ConfirmView,
    SwapParentsView,
)
from .utils import (
    MENTOR_TOTAL_STEPS,
    TOTAL_STEPS,
    _auto_authorize_handoff,
    _get_application_or_404,
    _is_handoff_authorized,
    _is_mentor,
    _issue_and_send,
    _mentor_progress,
    _redirect_after_email_verified,
    _redirect_to_current_step,
    _require_mentor,
    _require_mentor_verified,
    _require_verified_email,
    _sanitize_payload,
    _save_step_data,
    _student_initial_for,
    logger,
)
from .welcome import (
    ApplicationWithdrawView,
    ContinueView,
    ResumeLinkView,
    ResumeView,
    SubmittedView,
    WelcomeView,
)

__all__ = [
    # constants
    "MENTOR_TOTAL_STEPS",
    "TOTAL_STEPS",
    # helpers
    "_auto_authorize_handoff",
    "_get_application_or_404",
    "_is_mentor",
    "_is_handoff_authorized",
    "_issue_and_send",
    "_mentor_progress",
    "_redirect_after_email_verified",
    "_redirect_to_current_step",
    "_require_mentor",
    "_require_mentor_verified",
    "_require_verified_email",
    "_save_step_data",
    "_sanitize_payload",
    "_student_initial_for",
    "logger",
    # welcome
    "ApplicationWithdrawView",
    "ContinueView",
    "ResumeLinkView",
    "ResumeView",
    "SubmittedView",
    "WelcomeView",
    # steps_student
    "Step2ApplicantTypeView",
    "Step3DuplicateFoundView",
    "Step3ResendCodeView",
    "Step3VerifyEmailView",
    "Step4ProgramView",
    "Step5StudentInfoView",
    "Step6ExperienceView",
    "Step7PrimaryParentView",
    "Step8SecondaryParentView",
    "Step9ConfirmView",
    "SwapParentsView",
    # documents
    "Step10DocumentsView",
    # steps_mentor
    "MentorBlockedView",
    "MentorClearanceDetailView",
    "MentorClearanceInterestView",
    "MentorConfirmView",
    "MentorInfoView",
    # review
    "ApplicationApproveView",
    "ApplicationCleanupView",
    "ApplicationConvertView",
    "ApplicationDataEditForm",
    "ApplicationDeclineView",
    "ApplicationDeleteView",
    "ApplicationEditView",
    "ApplicationEmailView",
    "ApplicationResendEmailView",
    "ApplicationReviewDetailView",
    "ApplicationReviewListView",
    "DeclineForm",
    "REVIEW_PERM",
    "_ReviewerRequiredMixin",
]
