from django import forms
from .models import Badge

class BadgeForm(forms.ModelForm):
    class Meta:
        model = Badge
        fields = ["name","icon","category","level","description","skills_required","how_to_earn","prerequisites"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Prototyping"}),
            "category": forms.Select(attrs={"class": "form-select"}),
            "level": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Short public description of the badge"}),
            "skills_required": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Visible to students — list skills they must demonstrate"}),
            "how_to_earn": forms.Textarea(attrs={"class": "form-control", "rows": 4, "placeholder": "Mentor-only — how to test/verify (not shown to students)"}),
            "prerequisites": forms.SelectMultiple(attrs={"class": "form-select", "size": 6}),
            "icon": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }
        help_texts = {
            "icon": "PNG/JPG, 64×64–256×256 recommended. Leave blank to use default award icon.",
            "prerequisites": "Hold Ctrl/Cmd to select multiple. Leave empty if none.",
        }
