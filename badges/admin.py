from django.contrib import admin
from .models import Badge, StudentBadge

@admin.register(Badge)
class BadgeAdmin(admin.ModelAdmin):
    list_display = ("name", "level", "category")
    list_filter = ("category",)

@admin.register(StudentBadge)
class StudentBadgeAdmin(admin.ModelAdmin):
    list_display = ("student", "badge", "awarded_by", "awarded_at")
