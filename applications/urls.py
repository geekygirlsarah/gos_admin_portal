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
    # Wizard steps. Each step is keyed by application_id so the URL is
    # shareable / bookmarkable for the same in-progress application.
    path(
        "<str:app_id>/step2/",
        views.Step2ApplicantTypeView.as_view(),
        name="apply_step2",
    ),
    path(
        "<str:app_id>/step3/",
        views.Step3ProgramView.as_view(),
        name="apply_step3",
    ),
    path(
        "<str:app_id>/step4/",
        views.Step4VerifyEmailView.as_view(),
        name="apply_step4",
    ),
    path(
        "<str:app_id>/resend-code/",
        views.Step4ResendCodeView.as_view(),
        name="apply_step4_resend",
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
        views.Step6PrimaryParentView.as_view(),
        name="apply_step6",
    ),
    path(
        "<str:app_id>/step7/",
        views.Step7SecondaryParentView.as_view(),
        name="apply_step7",
    ),
    path(
        "<str:app_id>/step8/",
        views.Step8ConfirmView.as_view(),
        name="apply_step8",
    ),
    path(
        "<str:app_id>/submitted/",
        views.SubmittedView.as_view(),
        name="apply_submitted",
    ),
    # Step 9: post-approval signed-document download & re-upload page.
    # Only reachable once the application has been APPROVED by a lead
    # mentor; otherwise the view redirects to the applicant's current step.
    path(
        "<str:app_id>/step9/",
        views.Step9DocumentsView.as_view(),
        name="apply_step9",
    ),
]
