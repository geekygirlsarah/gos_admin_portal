from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Prefetch, Q, Value
from django.db.models.functions import Coalesce, NullIf
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from programs.models import Adult, Student
from programs.permission_views import can_user_delete, can_user_write

from ..models import RFIDCard


@login_required
def rfid_management_view(request):
    org = getattr(request, "organization", None)
    if not can_user_write(request.user, "attendance", org=org):
        messages.error(request, "You do not have permission to manage RFID cards.")
        return redirect("home")

    search_query = request.GET.get("q", "").strip()
    results = []
    assigned_cards = []
    if search_query:
        # Search students
        student_qs = (
            Student.objects.for_organization(org)
            .filter(
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
            .prefetch_related(
                Prefetch(
                    "rfid_cards",
                    queryset=RFIDCard.objects.for_organization(org).filter(
                        is_active=True
                    ),
                )
            )
        )
        for s in student_qs[:10]:
            results.append(
                {
                    "person": s,
                    "type": "student",
                    "rfid": s.rfid_cards.all().first(),
                }
            )

        # Search mentors
        adult_qs = (
            Adult.objects.for_organization(org)
            .filter(is_mentor=True)
            .filter(
                Q(legal_first_name__icontains=search_query)
                | Q(preferred_first_name__icontains=search_query)
                | Q(last_name__icontains=search_query)
            )
            .prefetch_related(
                Prefetch(
                    "rfid_cards",
                    queryset=RFIDCard.objects.for_organization(org).filter(
                        is_active=True
                    ),
                )
            )
        )
        for a in adult_qs[:10]:
            results.append(
                {
                    "person": a,
                    "type": "mentor",
                    "rfid": a.rfid_cards.all().first(),
                }
            )
    else:
        assigned_cards = RFIDCard.objects.for_organization(org).filter(is_active=True)
        if org is not None:
            assigned_cards = assigned_cards.filter(
                Q(student__organization=org) | Q(adult__organization=org)
            )
        assigned_cards = assigned_cards.select_related("student", "adult")
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
                            person = get_object_or_404(
                                Student.objects.for_organization(org), pk=person_id
                            )
                        else:
                            person = get_object_or_404(
                                Adult.objects.for_organization(org).filter(
                                    is_mentor=True
                                ),
                                pk=person_id,
                            )

                        # Find a tenant-owned card even if its owner link is
                        # inconsistent, so it cannot be overwritten implicitly.
                        tenant_cards = RFIDCard.objects.for_organization(
                            org or person.organization
                        ).select_related("student", "adult")
                        existing_card = tenant_cards.filter(uid=uid).first()
                        if existing_card is None:
                            stripped_uid = uid.lstrip("0")
                            if stripped_uid and stripped_uid != uid:
                                existing_card = tenant_cards.filter(
                                    uid=stripped_uid
                                ).first()

                        if existing_card:
                            old_owner = existing_card.student or existing_card.adult
                            if (
                                org is not None
                                and old_owner
                                and old_owner.organization_id != org.pk
                            ):
                                messages.error(
                                    request,
                                    "This card has an inconsistent assignment and cannot be changed here.",
                                )
                                return redirect(
                                    f"{reverse('rfid_management')}?{urlencode({'q': search_query})}"
                                )

                        # Deactivate other active cards for this person
                        person_cards = person.rfid_cards.filter(is_active=True)
                        if org is not None:
                            person_cards = person_cards.filter(organization=org)
                        if existing_card:
                            person_cards = person_cards.exclude(pk=existing_card.pk)
                        person_cards.update(is_active=False)

                        if existing_card:
                            # Reassign existing card
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
                                RFIDCard.objects.create(
                                    organization=org or person.organization,
                                    uid=uid,
                                    student=person,
                                )
                            else:
                                RFIDCard.objects.create(
                                    organization=org or person.organization,
                                    uid=uid,
                                    adult=person,
                                )
                            messages.success(
                                request, f"Assigned RFID {uid} to {person}."
                            )
                except Exception as e:
                    messages.error(request, f"Error assigning RFID: {e}")
            return redirect(
                f"{reverse('rfid_management')}?{urlencode({'q': search_query})}"
            )

        elif action == "deactivate":
            if not can_user_delete(request.user, "attendance", org=org):
                messages.error(
                    request, "You do not have permission to deactivate RFID cards."
                )
                return redirect(
                    f"{reverse('rfid_management')}?{urlencode({'q': search_query})}"
                )
            card_id = request.POST.get("card_id")
            card = get_object_or_404(RFIDCard.objects.for_organization(org), id=card_id)
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
