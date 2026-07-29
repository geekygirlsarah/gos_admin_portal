from django import forms
from django.forms.widgets import SelectMultiple
from django.utils.safestring import mark_safe


class DualListboxWidget(SelectMultiple):
    template_name = "programs/widgets/dual_listbox.html"

    def __init__(
        self,
        attrs=None,
        choices=(),
        available_label=None,
        selected_label=None,
        show_search=True,
    ):
        super().__init__(attrs, choices)
        self.available_label = available_label
        self.selected_label = selected_label
        self.show_search = show_search

    class Media:
        css = {
            "all": ("css/dual-listbox.css",),
        }
        js = ("js/dual-listbox.js",)

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        # We need to separate options into 'available' and 'selected'
        options = context["widget"]["optgroups"]
        available_options = []
        selected_options = []

        selected_values = set()
        for v in value or []:
            if hasattr(v, "pk"):
                selected_values.add(str(v.pk))
            else:
                selected_values.add(str(v))

        for group in options:
            for option in group[1]:
                if str(option["value"]) in selected_values:
                    selected_options.append(option)
                else:
                    available_options.append(option)

        context["available_options"] = available_options
        context["selected_options"] = selected_options
        context["available_label"] = self.available_label or "Available"
        context["selected_label"] = self.selected_label or "Selected"
        context["show_search"] = self.show_search
        return context
