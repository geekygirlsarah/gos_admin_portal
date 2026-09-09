import datetime

from django import forms
from django.utils import timezone


def group_programs_by_status(programs_or_queryset):
    """Categorize a collection or queryset of Program instances into Active, Upcoming, and Past groups.

    - Active: status == 'Active' (active=True and start_date <= today <= end_date or ongoing),
      sorted alphabetically by name.
    - Upcoming: status == 'Upcoming' (active=True and start_date > today),
      sorted chronologically by start_date ascending (soonest first).
    - Past: status == 'Inactive' (active=False or end_date < today),
      sorted reverse-chronologically (newest past programs first).
    """
    programs = list(programs_or_queryset)
    active = []
    upcoming = []
    past = []

    for p in programs:
        st = p.status
        if st == "Active":
            active.append(p)
        elif st == "Upcoming":
            upcoming.append(p)
        else:
            past.append(p)

    active.sort(key=lambda p: (p.name or "").lower())
    upcoming.sort(
        key=lambda p: (
            p.start_date or datetime.date.max,
            (p.name or "").lower(),
        )
    )

    def _past_sort_key(p):
        d = p.end_date or p.start_date or datetime.date.min
        return (-d.toordinal(), (p.name or "").lower())

    past.sort(key=_past_sort_key)

    return {
        "active": active,
        "upcoming": upcoming,
        "past": past,
    }


def get_grouped_program_choices(
    programs_or_queryset, empty_label=None, include_past=True, label_format=None
):
    """Return a choice list formatted with optgroup tuples for Django form fields."""
    grouped = group_programs_by_status(programs_or_queryset)
    choices = []
    if empty_label is not None:
        choices.append(("", empty_label))

    def format_label(p):
        if label_format:
            return label_format(p)
        if p.year_display:
            return f"{p.name} ({p.year_display})"
        return p.name

    if grouped["active"]:
        choices.append(
            ("Active Programs", [(p.pk, format_label(p)) for p in grouped["active"]])
        )
    if grouped["upcoming"]:
        choices.append(
            (
                "Upcoming Programs",
                [(p.pk, format_label(p)) for p in grouped["upcoming"]],
            )
        )
    if include_past and grouped["past"]:
        choices.append(
            (
                "Past / Archived Programs",
                [(p.pk, format_label(p)) for p in grouped["past"]],
            )
        )

    return choices


class GroupedProgramModelChoiceIterator(forms.models.ModelChoiceIterator):
    """Model choice iterator that groups Program options into Active, Upcoming, and Past optgroups."""

    def __iter__(self):
        if self.field.empty_label is not None:
            yield ("", self.field.empty_label)

        queryset = self.queryset
        if hasattr(queryset, "all"):
            queryset = queryset.all()

        grouped = group_programs_by_status(queryset)

        def format_label(p):
            return self.field.label_from_instance(p)

        if grouped["active"]:
            yield (
                "Active Programs",
                [(p.pk, format_label(p)) for p in grouped["active"]],
            )
        if grouped["upcoming"]:
            yield (
                "Upcoming Programs",
                [(p.pk, format_label(p)) for p in grouped["upcoming"]],
            )
        if getattr(self.field, "include_past", True) and grouped["past"]:
            yield (
                "Past / Archived Programs",
                [(p.pk, format_label(p)) for p in grouped["past"]],
            )


class GroupedProgramChoiceField(forms.ModelChoiceField):
    """A ModelChoiceField for Programs that automatically renders choices in
    Active, Upcoming, and Past / Archived optgroups.
    """

    iterator = GroupedProgramModelChoiceIterator

    def __init__(self, *args, include_past=True, **kwargs):
        self.include_past = include_past
        super().__init__(*args, **kwargs)

    def label_from_instance(self, obj):
        if obj.year_display:
            return f"{obj.name} ({obj.year_display})"
        return obj.name
