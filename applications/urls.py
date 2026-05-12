"""Public URLs for the application wizard.

Mounted at ``/apply/`` from the project urlconf.
"""
from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
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
]
