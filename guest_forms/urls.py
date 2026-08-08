"""URL configuration for guest forms.

Public URLs are mounted at /guest/ from the project urlconf.
Admin/Review URLs are mounted at /programs/guest-forms/ (or similar).
"""

from django.urls import path

from . import views

# Public URLs (no login required)
public_urlpatterns = [
    path("", views.GuestFormIndexView.as_view(), name="guest_form_index"),
    path(
        "form/<slug:slug>/",
        views.GuestFormDetailView.as_view(),
        name="guest_form_detail",
    ),
    path(
        "form/<slug:slug>/submitted/",
        views.GuestFormSubmittedView.as_view(),
        name="guest_form_submitted",
    ),
]

# Admin/Review URLs (require login + review_guestform permission)
review_urlpatterns = [
    # Review queue
    path(
        "review/",
        views.GuestFormReviewListView.as_view(),
        name="guest_form_review_list",
    ),
    path(
        "review/<int:submission_id>/",
        views.GuestFormReviewDetailView.as_view(),
        name="guest_form_review_detail",
    ),
    # Management
    path(
        "manage/",
        views.GuestFormManageListView.as_view(),
        name="guest_form_manage_list",
    ),
    path("manage/new/", views.GuestFormCreateView.as_view(), name="guest_form_create"),
    path(
        "manage/<int:form_id>/edit/",
        views.GuestFormUpdateView.as_view(),
        name="guest_form_edit",
    ),
    path(
        "manage/<int:form_id>/delete/",
        views.GuestFormDeleteView.as_view(),
        name="guest_form_delete",
    ),
]

# Combined for easy inclusion
urlpatterns = public_urlpatterns + review_urlpatterns
