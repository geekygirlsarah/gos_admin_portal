from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Value
from django.db.models.functions import Coalesce, NullIf
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from programs.models import Adult, Student
from programs.permission_views import can_user_delete, can_user_write

from ..models import RFIDCard
from ..services import find_card_by_uid


@login_required
def rfid_management_view(request):
    if not can_user_write(request.user, "attendance"):
        messages.error(request, "You do not have permission to manage RFID cards.")
        return redirect("home")

    search_query = request.GET.get("q", "").strip()
    results = []
    assigned_cards = []
    if search_query:
        # Search students
        student_qs = (
            Student.objects.filter(
                Q(preferred_first_name__icontains=search_query)
                | Q(last_name__icontains=search_query)
                | Q(legal_first_name__icontains=search_query)
            )
            .annotate(
                sort_first=Coalesce(
                    NullIf("preferred_first_name", Value("")), "legal_first_name"
                ),
            )
            .order_by("sort_first", "last_name")
            .prefetch_related("rfid_cards")
        )
        for s in student_qs[:10]:
            results.append(
                {
                    "person": s,
                    "type": "student",
                    "rfid": s.rfid_cards.filter(is_active=True).first(),
                }
            )

        # Search mentors
        adult_qs = (
            Adult.objects.filter(is_mentor=True)
            .filter(
                Q(legal_first_name__icontains=search_query)
                | Q(preferred_first_name__icontains=search_query)
                | Q(last_name__icontains=search_query)
            )
            .prefetch_related("rfid_cards")
        )
        for a in adult_qs[:10]:
            results.append(
                {
                    "person": a,
                    "type": "mentor",
                    "rfid": a.rfid_cards.filter(is_active=True).first(),
                }
            )
    else:
        assigned_cards = RFIDCard.objects.filter(is_active=True).select_related(
            "student", "adult"
        )
    # Sort assigned cards by preferred name then last name
    assigned_cards_list = list(assigned_cards)
    assigned_cards_list.sort(
        key=lambda c: (
            (
                c.student.full_name
                if c.student
                else c.adult.full_name if c.adult else ""
            ).lower()
        )
    )
    assigned_cards = assigned_cards_list
    # Sort results by preferred name then last name, regardless of type
    results.sort(key=lambda r: (r["person"].full_name or "").lower())

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "assign":
            person_type = request.POST.get("person_type")
            person_id = request.POST.get("person_id")
            uid = request.POST.get("uid", "").strip()

            if not uid:
                messages.error(request, "RFID UID cannot be empty.")
            else:
                try:
                    with transaction.atomic():
                        # Find person
                        if person_type == "student":
                            person = get_object_or_404(Student, pk=person_id)
                        else:
                            person = get_object_or_404(Adult, pk=person_id)

                        # Find if this UID is already in the system
                        existing_card = find_card_by_uid(uid)

                        # Deactivate other active cards for this person
                        person_cards = person.rfid_cards.filter(is_active=True)
                        if existing_card:
                            person_cards = person_cards.exclude(pk=existing_card.pk)
                        person_cards.update(is_active=False)

                        if existing_card:
                            # Reassign existing card
                            old_owner = existing_card.student or existing_card.adult
                            existing_card.student = (
                                person if person_type == "student" else None
                            )
                            existing_card.adult = (
                                person if person_type != "student" else None
                            )
                            existing_card.is_active = True
                            existing_card.uid = uid  # Normalize to full UID
                            existing_card.assigned_at = timezone.now()
                            existing_card.save()

                            if old_owner and old_owner != person:
                                messages.success(
                                    request,
                                    f"Reassigned RFID {uid} from {old_owner} to {person}.",
                                )
                            else:
                                messages.success(
                                    request, f"Assigned RFID {uid} to {person}."
                                )
                        else:
                            # Create new card record
                            if person_type == "student":
                                RFIDCard.objects.create(uid=uid, student=person)
                            else:
                                RFIDCard.objects.create(uid=uid, adult=person)
                            messages.success(
                                request, f"Assigned RFID {uid} to {person}."
                            )
                except Exception as e:
                    messages.error(request, f"Error assigning RFID: {e}")
            return redirect(
                f"{reverse('rfid_management')}?{urlencode({'q': search_query})}"
            )

        elif action == "deactivate":
            if not can_user_delete(request.user, "attendance"):
                messages.error(
                    request, "You do not have permission to deactivate RFID cards."
                )
                return redirect(
                    f"{reverse('rfid_management')}?{urlencode({'q': search_query})}"
                )
            card_id = request.POST.get("card_id")
            card = get_object_or_404(RFIDCard, id=card_id)
            card.is_active = False
            card.save()
            messages.success(request, f"Deactivated RFID {card.uid}")
            return redirect(
                f"{reverse('rfid_management')}?{urlencode({'q': search_query})}"
            )

    return render(
        request,
        "attendance/rfid_management.html",
        {
            "results": results,
            "assigned_cards": assigned_cards,
            "q": search_query,
        },
    )
