from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_http_methods

from ..models import Adult, Student
from ..validators import validate_andrew_id


def _person_sort_key(person):
    return (person.full_name or "").lower()


def _get_adult_sponsor_choices():
    return Adult.objects.filter(Q(andrew_id__isnull=False) & ~Q(andrew_id="")).order_by(
        "first_name", "last_name"
    )


@login_required
@require_http_methods(["GET", "POST"])
def andrew_id_management_view(request):
    if not (
        request.user.is_superuser
        or request.user.groups.filter(name="LeadMentor").exists()
    ):
        messages.error(request, "You do not have permission to manage Andrew IDs.")
        return redirect("home")

    search_query = request.GET.get("q", "").strip()
    results = []
    assigned_people = []

    if search_query:
        student_qs = Student.objects.filter(
            Q(first_name__icontains=search_query)
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

        adult_qs = Adult.objects.filter(
            Q(first_name__icontains=search_query) | Q(last_name__icontains=search_query)
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
        assigned_people_qs = Student.objects.filter(
            Q(andrew_id__isnull=False) & ~Q(andrew_id="")
        ) | Student.objects.filter(Q(andrew_email__isnull=False) & ~Q(andrew_email=""))
        adult_assigned = Adult.objects.filter(
            Q(andrew_id__isnull=False) & ~Q(andrew_id="")
        ) | Adult.objects.filter(Q(andrew_email__isnull=False) & ~Q(andrew_email=""))

        assigned_people = []
        for s in assigned_people_qs:
            assigned_people.append({"person": s, "type": "student"})
        for a in adult_assigned:
            assigned_people.append({"person": a, "type": "adult"})

        assigned_people.sort(key=lambda r: _person_sort_key(r["person"]))

    sponsor_choices = _get_adult_sponsor_choices()

    if request.method == "POST":
        action = request.POST.get("action")
        person_type = request.POST.get("person_type")
        person_id = request.POST.get("person_id")

        if action == "set":
            raw_id = request.POST.get("andrew_id", "").strip().lower()

            if not raw_id:
                messages.error(request, "Andrew ID cannot be empty.")
                return _redirect_back(search_query)

            try:
                validate_andrew_id(raw_id)
            except ValidationError as e:
                messages.error(request, e.message)
                return _redirect_back(search_query)

            if person_type == "student":
                person = get_object_or_404(Student, pk=person_id)
            else:
                person = get_object_or_404(Adult, pk=person_id)

            existing_student = Student.objects.filter(andrew_id__iexact=raw_id).exclude(
                pk=person.pk if person_type == "student" else None
            )
            if person_type == "student":
                existing_student = existing_student.exclude(pk=person.pk)
            if existing_student.exists():
                occupied = existing_student.first()
                messages.error(
                    request,
                    f"Andrew ID '{raw_id}' is already assigned to student {occupied}.",
                )
                return _redirect_back(search_query)

            existing_adult = Adult.objects.filter(andrew_id__iexact=raw_id)
            if person_type == "adult":
                existing_adult = existing_adult.exclude(pk=person.pk)
            if existing_adult.exists():
                occupied = existing_adult.first()
                messages.error(
                    request,
                    f"Andrew ID '{raw_id}' is already assigned to adult {occupied}.",
                )
                return _redirect_back(search_query)

            auto_email = f"{raw_id}@andrew.cmu.edu"
            person.andrew_id = raw_id
            person.andrew_email = auto_email

            if person_type == "adult":
                expiration = request.POST.get("andrew_id_expiration", "").strip()
                sponsor_id = request.POST.get("andrew_id_sponsor", "").strip()
                if expiration:
                    person.andrew_id_expiration = expiration
                else:
                    person.andrew_id_expiration = None
                if sponsor_id:
                    person.andrew_id_sponsor = get_object_or_404(Adult, pk=sponsor_id)
                else:
                    person.andrew_id_sponsor = None

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

            messages.success(request, f"Assigned Andrew ID '{raw_id}' to {person}.")
            return _redirect_back(search_query)

        elif action == "clear":
            if person_type == "student":
                person = get_object_or_404(Student, pk=person_id)
            else:
                person = get_object_or_404(Adult, pk=person_id)

            old_id = person.andrew_id or ""
            person.andrew_id = None
            person.andrew_email = None
            if person_type == "adult":
                person.andrew_id_expiration = None
                person.andrew_id_sponsor = None
            person.save()

            messages.success(
                request,
                f"Cleared Andrew ID '{old_id}' from {person}.",
            )
            return _redirect_back(search_query)

    return render(
        request,
        "programs/andrew_id_management.html",
        {
            "results": results,
            "assigned_people": assigned_people,
            "q": search_query,
            "sponsor_choices": sponsor_choices,
        },
    )


def _redirect_back(search_query):
    url = reverse("andrew_id_management")
    if search_query:
        url = f"{url}?{urlencode({'q': search_query})}"
    return redirect(url)
