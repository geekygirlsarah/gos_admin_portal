from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q, Value
from django.db.models.functions import Coalesce, Lower, NullIf
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_http_methods

from ..models import Adult, Student
from ..validators import validate_org_account_id


def _person_sort_key(person):
    return (person.full_name or "").lower()


def _get_adult_sponsor_choices(organization=None):
    return (
        Adult.objects.for_organization(organization)
        .filter(is_mentor=True)
        .order_by(
            Lower(
                Coalesce(NullIf("preferred_first_name", Value("")), "legal_first_name")
            ),
            Lower("last_name"),
        )
    )


@login_required
@require_http_methods(["GET", "POST"])
def org_email_management_view(request):
    from organizations.utils import (
        get_default_organization,
        get_organization_setting,
    )

    from ..permission_views import user_is_lead_mentor

    org = getattr(request, "organization", None) or get_default_organization()
    org_account_name = (
        get_organization_setting(org, "org_account_name", "Andrew ID")
        if org
        else "Andrew ID"
    )

    if not user_is_lead_mentor(request.user, org=org):
        messages.error(
            request, f"You do not have permission to manage {org_account_name}s."
        )
        return redirect("home")

    search_query = request.GET.get("q", "").strip()
    results = []
    assigned_people = []

    if search_query:
        student_qs = Student.objects.for_organization(org).filter(
            Q(preferred_first_name__icontains=search_query)
            | Q(last_name__icontains=search_query)
            | Q(legal_first_name__icontains=search_query)
        )
        for s in student_qs[:20]:
            results.append(
                {
                    "person": s,
                    "type": "student",
                }
            )

        adult_qs = Adult.objects.for_organization(org).filter(
            Q(preferred_first_name__icontains=search_query)
            | Q(legal_first_name__icontains=search_query)
            | Q(last_name__icontains=search_query)
        )
        for a in adult_qs[:20]:
            results.append(
                {
                    "person": a,
                    "type": "adult",
                }
            )

        results.sort(key=lambda r: _person_sort_key(r["person"]))
    else:
        assigned_people_qs = Student.objects.for_organization(org).filter(
            (Q(org_account_id__isnull=False) & ~Q(org_account_id=""))
            | (Q(org_email__isnull=False) & ~Q(org_email=""))
        )
        adult_assigned = Adult.objects.for_organization(org).filter(
            (Q(org_account_id__isnull=False) & ~Q(org_account_id=""))
            | (Q(org_email__isnull=False) & ~Q(org_email=""))
        )

        assigned_people = []
        for s in assigned_people_qs:
            assigned_people.append({"person": s, "type": "student"})
        for a in adult_assigned:
            assigned_people.append({"person": a, "type": "adult"})

        assigned_people.sort(key=lambda r: _person_sort_key(r["person"]))

    sponsor_choices = _get_adult_sponsor_choices(org)

    if request.method == "POST":
        action = request.POST.get("action")
        person_type = request.POST.get("person_type")
        person_id = request.POST.get("person_id")

        if action == "set":
            raw_id = (
                (
                    request.POST.get("org_account_id")
                    or request.POST.get("andrew_id")
                    or ""
                )
                .strip()
                .lower()
            )

            if not raw_id:
                messages.error(request, f"{org_account_name} cannot be empty.")
                return _redirect_back(search_query)

            try:
                validate_org_account_id(raw_id)
            except ValidationError as e:
                messages.error(request, e.message)
                return _redirect_back(search_query)

            if person_type == "student":
                person = get_object_or_404(
                    Student.objects.for_organization(org), pk=person_id
                )
            elif person_type == "adult":
                person = get_object_or_404(
                    Adult.objects.for_organization(org), pk=person_id
                )
            else:
                messages.error(request, "Invalid account holder type.")
                return _redirect_back(search_query)

            person_org = (
                getattr(person, "organization", None)
                or org
                or get_default_organization()
            )
            person_account_name = (
                get_organization_setting(
                    person_org, "org_account_name", org_account_name
                )
                if person_org
                else org_account_name
            )

            existing_student = Student.objects.for_organization(org).filter(
                org_account_id__iexact=raw_id
            )
            if person_type == "student":
                existing_student = existing_student.exclude(pk=person.pk)
            if existing_student.exists():
                occupied = existing_student.first()
                messages.error(
                    request,
                    f"{person_account_name} '{raw_id}' is already assigned to student {occupied}.",
                )
                return _redirect_back(search_query)

            existing_adult = Adult.objects.for_organization(org).filter(
                org_account_id__iexact=raw_id
            )
            if person_type == "adult":
                existing_adult = existing_adult.exclude(pk=person.pk)
            if existing_adult.exists():
                occupied = existing_adult.first()
                messages.error(
                    request,
                    f"{person_account_name} '{raw_id}' is already assigned to adult {occupied}.",
                )
                return _redirect_back(search_query)

            domain_setting = (
                get_organization_setting(
                    person_org, "org_email_domain", "andrew.cmu.edu"
                )
                or "andrew.cmu.edu"
            )
            primary_domain = (
                domain_setting.replace(";", ",").split(",")[0].strip().lstrip("@")
                or "andrew.cmu.edu"
            )
            auto_email = f"{raw_id}@{primary_domain}"
            person.org_account_id = raw_id
            person.org_email = auto_email

            expiration = (
                request.POST.get("org_email_expiration")
                or request.POST.get("andrew_id_expiration")
                or ""
            ).strip()
            sponsor_id = (
                request.POST.get("org_email_sponsor")
                or request.POST.get("andrew_id_sponsor")
                or ""
            ).strip()

            if expiration:
                person.org_email_expiration = expiration
            else:
                person.org_email_expiration = None
            if sponsor_id:
                person.org_email_sponsor = get_object_or_404(
                    Adult.objects.for_organization(org), pk=sponsor_id
                )
            else:
                person.org_email_sponsor = None

            try:
                person.full_clean()
                person.save()
            except ValidationError as e:
                msg = "; ".join(
                    f"{field}: {err}"
                    for field, err in e.message_dict.items()
                    if field != "__all__"
                ) or str(e)
                messages.error(request, f"Validation error: {msg}")
                return _redirect_back(search_query)

            messages.success(
                request, f"Assigned {person_account_name} '{raw_id}' to {person}."
            )
            return _redirect_back(search_query)

        elif action == "clear":
            if person_type == "student":
                person = get_object_or_404(
                    Student.objects.for_organization(org), pk=person_id
                )
            elif person_type == "adult":
                person = get_object_or_404(
                    Adult.objects.for_organization(org), pk=person_id
                )
            else:
                messages.error(request, "Invalid account holder type.")
                return _redirect_back(search_query)

            person_org = (
                getattr(person, "organization", None)
                or org
                or get_default_organization()
            )
            person_account_name = (
                get_organization_setting(
                    person_org, "org_account_name", org_account_name
                )
                if person_org
                else org_account_name
            )

            old_id = person.org_account_id or ""
            person.org_account_id = None
            person.org_email = None
            person.org_email_expiration = None
            person.org_email_sponsor = None
            person.save()

            messages.success(
                request, f"Removed {person_account_name} '{old_id}' from {person}."
            )
            return _redirect_back(search_query)

        else:
            messages.error(request, "Unknown action.")
            return _redirect_back(search_query)

    context = {
        "q": search_query,
        "search_query": search_query,
        "results": results,
        "assigned_people": assigned_people,
        "sponsor_choices": sponsor_choices,
        "org_account_name": org_account_name,
    }
    return render(request, "programs/org_email_management.html", context)


def _redirect_back(search_query):
    url = reverse("org_email_management")
    if search_query:
        url += "?" + urlencode({"q": search_query})
    return redirect(url)


# Backward-compatibility alias
andrew_id_management_view = org_email_management_view
