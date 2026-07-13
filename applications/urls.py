"""Public URLs for the application wizard.

Mounted at ``/apply/`` from the project urlconf.
"""

from __future__ import annotations

from django.urls import path

from . import review, views

urlpatterns = [
    # --- Lead-mentor review pages (gated by applications.review_application) ---
    path(
        "review/",
        review.ApplicationReviewListView.as_view(),
        name="application_review_list",
    ),
    path(
        "review/cleanup-stale/",
        review.ApplicationCleanupView.as_view(),
        name="application_cleanup_stale",
    ),
    path(
        "review/<str:app_id>/",
        review.ApplicationReviewDetailView.as_view(),
        name="application_review_detail",
    ),
    path(
        "review/<str:app_id>/approve/",
        review.ApplicationApproveView.as_view(),
        name="application_review_approve",
    ),
    path(
        "review/<str:app_id>/decline/",
        review.ApplicationDeclineView.as_view(),
        name="application_review_decline",
    ),
    path(
        "review/<str:app_id>/edit/",
        review.ApplicationEditView.as_view(),
        name="application_review_edit",
    ),
    path(
        "review/<str:app_id>/delete/",
        review.ApplicationDeleteView.as_view(),
        name="application_review_delete",
    ),
    path(
        "review/<str:app_id>/convert/",
        review.ApplicationConvertView.as_view(),
        name="application_review_convert",
    ),
    # Step 1: welcome / start / resume
    path("", views.WelcomeView.as_view(), name="apply_start"),
    path("resume/", views.ResumeView.as_view(), name="apply_resume"),
    # Resume from a link in an email (no form, just lands on the wizard)
    path(
        "r/<str:app_id>/",
        views.ResumeLinkView.as_view(),
        name="apply_resume_link",
    ),
    path(
        "r/<str:app_id>/<str:token>/",
        views.ResumeLinkView.as_view(),
        name="apply_resume_link_with_token",
    ),
    # Wizard steps. Each step is keyed by application_id so the URL is
    # shareable / bookmarkable for the same in-progress application.
    path(
        "<str:app_id>/step2/",
        views.Step2ApplicantTypeView.as_view(),
        name="apply_step2",
    ),
    path(
        "<str:app_id>/step3/",
        views.Step3VerifyEmailView.as_view(),
        name="apply_step3",
    ),
    path(
        "<str:app_id>/step3/duplicate-found/",
        views.Step3DuplicateFoundView.as_view(),
        name="apply_duplicate_found",
    ),
    path(
        "<str:app_id>/step4/",
        views.Step4ProgramView.as_view(),
        name="apply_step4",
    ),
    path(
        "<str:app_id>/resend-code/",
        views.Step3ResendCodeView.as_view(),
        name="apply_step3_resend",
    ),
    # Continue alias — drops the user back into the wizard at their step.
    path(
        "<str:app_id>/continue/",
        views.ContinueView.as_view(),
        name="apply_continue",
    ),
    path(
        "<str:app_id>/step5/",
        views.Step5StudentInfoView.as_view(),
        name="apply_step5",
    ),
    path(
        "<str:app_id>/step6/",
        views.Step6ExperienceView.as_view(),
        name="apply_step6",
    ),
    path(
        "<str:app_id>/step7/",
        views.Step7PrimaryParentView.as_view(),
        name="apply_step7",
    ),
    path(
        "<str:app_id>/step8/",
        views.Step8SecondaryParentView.as_view(),
        name="apply_step8",
    ),
    path(
        "<str:app_id>/swap-parents/",
        views.SwapParentsView.as_view(),
        name="apply_swap_parents",
    ),
    path(
        "<str:app_id>/step9/",
        views.Step9ConfirmView.as_view(),
        name="apply_step9",
    ),
    path(
        "<str:app_id>/submitted/",
        views.SubmittedView.as_view(),
        name="apply_submitted",
    ),
    # Step 10: post-approval signed-document download & re-upload page.
    # Only reachable once the application has been APPROVED by a lead
    # mentor; otherwise the view redirects to the applicant's current step.
    path(
        "<str:app_id>/step10/",
        views.Step10DocumentsView.as_view(),
        name="apply_step10",
    ),
    # --- Mentor branch (applicant_type=MENTOR; skips Steps 3, 5-7, 9) ---
    path(
        "<str:app_id>/mentor/blocked/",
        views.MentorBlockedView.as_view(),
        name="apply_mentor_blocked",
    ),
    path(
        "<str:app_id>/mentor/info/",
        views.MentorInfoView.as_view(),
        name="apply_mentor_info",
    ),
    path(
        "<str:app_id>/mentor/clearances/interest/",
        views.MentorClearanceInterestView.as_view(),
        name="apply_mentor_clearance_interest",
    ),
    path(
        "<str:app_id>/mentor/clearances/detail/",
        views.MentorClearanceDetailView.as_view(),
        name="apply_mentor_clearance_detail",
    ),
    path(
        "<str:app_id>/mentor/confirm/",
        views.MentorConfirmView.as_view(),
        name="apply_mentor_confirm",
    ),
]
