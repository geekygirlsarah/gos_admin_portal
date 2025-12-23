from django import forms
from .models import ApiClientKey
import secrets


class ApiClientKeyForm(forms.ModelForm):
    generate_new_key = forms.BooleanField(
        required=False,
        initial=False,
        help_text="Check to generate a new random key on save (overwrites the current key).",
        label="Generate new key",
    )

    class Meta:
        model = ApiClientKey
        fields = ["name", "scope", "is_active", "key"]
        widgets = {
            "key": forms.TextInput(attrs={"maxlength": 64}),
        }
        help_texts = {
            "key": "Leave blank to auto-generate a secure key, or enter your own shared secret (min 16 chars recommended).",
        }

    def clean_key(self):
        key = self.cleaned_data.get("key", "") or ""
        if self.cleaned_data.get("generate_new_key"):
            return ""  # force regen in save()
        return key

    def save(self, commit=True):
        obj = super().save(commit=False)
        regen = self.cleaned_data.get("generate_new_key")
        if regen or not obj.key:
            # 32 bytes -> 64 hex chars
            obj.key = secrets.token_hex(32)
        if commit:
            obj.save()
        return obj
