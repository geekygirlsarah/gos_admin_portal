from django.contrib import admin
from .models import ApiClientKey


@admin.register(ApiClientKey)
class ApiClientKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "scope", "is_active", "key", "created_at", "last_used_at")
    list_filter = ("scope", "is_active")
    search_fields = ("name", "key")
