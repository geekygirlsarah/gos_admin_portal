import secrets

from django import forms

from .models import ApiClientKey


class ApiClientKeyForm(forms.ModelForm):
    class Meta:
        model = ApiClientKey
        fields = ["name", "scope", "is_active"]

    def save(self, commit=True):
        obj = super().save(commit=False)
        if not obj.key:
            # Auto-generate a secure key on creation (32 bytes -> 64 hex chars)
            obj.key = secrets.token_hex(32)
        if commit:
            obj.save()
        return obj
