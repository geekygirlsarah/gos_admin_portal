from django.contrib import admin

from .forms import StudentForm
from .models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Fee,
    Payment,
    Program,
    ProgramDocument,
    ProgramFeature,
    RolePermission,
    School,
    SlidingScale,
    Student,
)


@admin.register(ProgramFeature)
class ProgramFeatureAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "display_order")
    search_fields = ("name", "key")


@admin.register(ProgramDocument)
class ProgramDocumentAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "program",
        "is_required",
        "is_active",
        "display_order",
        "updated_at",
    )
    list_filter = ("program", "is_required", "is_active")
    search_fields = ("name", "program__name")
    autocomplete_fields = ("program",)


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display = ("role", "section", "can_read", "can_write")
    list_filter = ("role", "section")
    list_editable = ("can_read", "can_write")


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "year_display",
        "start_date",
        "end_date",
        "cost",
        "active",
        "updated_at",
    )
    search_fields = ("name",)
    list_filter = ("active", "start_date", "end_date")
    filter_horizontal = ("features",)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "description",
                    "active",
                    "start_date",
                    "end_date",
                    "grade_range_start",
                    "grade_range_end",
                    "cost",
                )
            },
        ),
        (
            "Features",
            {
                "fields": ("features",),
                "description": "Enable optional capabilities for this program.",
            },
        ),
        ("System", {"fields": ("created_at", "updated_at")}),
    )
    readonly_fields = ("created_at", "updated_at")


@admin.register(Fee)
class FeeAdmin(admin.ModelAdmin):
    list_display = ("program", "name", "amount", "date", "updated_at")
    list_filter = ("program", "date")
    search_fields = ("name", "program__name")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "program",
        "amount",
        "paid_on",
        "paid_via",
        "check_number",
        "created_at",
    )
    list_filter = ("program", "paid_on", "paid_via")
    search_fields = (
        "student__first_name",
        "student__last_name",
        "program__name",
    )
    autocomplete_fields = ("student", "program")


class EnrollmentInline(admin.TabularInline):
    model = Enrollment
    extra = 1
    fields = ("program", "team", "crew", "subteam", "active")


class AdultStudentRelationshipInline(admin.TabularInline):
    model = AdultStudentRelationship
    extra = 1
    autocomplete_fields = ("student",)


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ("name", "district", "street_address", "city", "state", "zip_code")
    search_fields = ("name", "district", "street_address", "city", "state", "zip_code")


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    form = StudentForm
    list_display = (
        "first_name",
        "legal_first_name",
        "last_name",
        "pronouns",
        "graduation_year",
        "andrew_id",
        "on_discord",
        "graduated",
        "updated_at",
    )
    list_filter = ("graduated", "on_discord", "seen_once", "graduation_year")
    search_fields = (
        "first_name",
        "legal_first_name",
        "last_name",
        "pronouns",
        "user__username",
        "user__email",
        "personal_email",
        "andrew_id",
        "andrew_email",
        "discord_handle",
        "school__name",
        "city",
        "state",
    )
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            "Identity",
            {
                "fields": (
                    "first_name",
                    "legal_first_name",
                    "last_name",
                    "pronouns",
                    "date_of_birth",
                )
            },
        ),
        (
            "Contact",
            {
                "fields": (
                    "address",
                    "city",
                    "state",
                    "zip_code",
                    "cell_phone_number",
                    "personal_email",
                )
            },
        ),
        (
            "Andrew",
            {
                "fields": (
                    "andrew_id",
                    "andrew_email",
                )
            },
        ),
        (
            "School",
            {
                "fields": (
                    "school",
                    "graduation_year",
                )
            },
        ),
        (
            "Other",
            {
                "fields": (
                    "race_ethnicities",
                    "tshirt_size",
                    "seen_once",
                    "on_discord",
                    "discord_handle",
                )
            },
        ),
        (
            "FIRST Website",
            {
                "fields": (
                    "first_has_account",
                    "first_attached_to_parent_account",
                    "first_signed_cr",
                    "first_registered_teams",
                )
            },
        ),
        (
            "System",
            {
                "fields": (
                    "active",
                    "graduated",
                    "created_at",
                    "updated_at",
                    "user",
                )
            },
        ),
    )
    inlines = [EnrollmentInline]

    actions = ["convert_to_alumni", "remove_alumni_flag"]

    def convert_to_alumni(self, request, queryset):
        """Create or update Adult records flagged as alumni for selected students.

        Marks the students as graduated and inactive.
        """
        from .utils import convert_student_to_alumni

        created = 0
        existed = 0
        marked_graduated = 0
        for student in queryset:
            _adult, was_created, was_marked = convert_student_to_alumni(student)
            if was_created:
                created += 1
            else:
                existed += 1
            if was_marked:
                marked_graduated += 1

        self.message_user(
            request,
            f"Adults created as Alumni: {created}, existing/updated: {existed}. "
            f"Selected students marked as graduated/inactive: {marked_graduated}.",
        )

    convert_to_alumni.short_description = (
        "Convert to Alumni (mark/create Adult and mark students graduated/inactive)"
    )

    def remove_alumni_flag(self, request, queryset):
        """Unset the is_alumni flag on matching Adult records for selected students (undo)."""
        from .utils import find_matching_alumni_adult

        unset = 0
        for student in queryset:
            adult = find_matching_alumni_adult(student)
            if adult and adult.is_alumni:
                adult.is_alumni = False
                adult.save(update_fields=["is_alumni", "updated_at"])
                unset += 1
        self.message_user(request, f"Adults unmarked as alumni: {unset}.")

    remove_alumni_flag.short_description = "Unmark matching Adults as Alumni (undo)"


@admin.register(Adult)
class ParentAdmin(admin.ModelAdmin):
    list_display = (
        "first_name",
        "preferred_first_name",
        "last_name",
        "is_parent",
        "is_mentor",
        "is_alumni",
        "student_record",
        "email",
        "phone_number",
        "email_updates",
    )
    list_filter = ("email_updates", "is_parent", "is_mentor", "is_alumni")
    search_fields = (
        "first_name",
        "preferred_first_name",
        "last_name",
        "email",
        "phone_number",
    )
    inlines = [AdultStudentRelationshipInline]


@admin.register(SlidingScale)
class SlidingScaleAdmin(admin.ModelAdmin):
    list_display = ("student", "program", "percent", "date", "is_pending", "updated_at")
    list_filter = ("program", "is_pending")
    search_fields = ("student__first_name", "student__last_name", "program__name")
    autocomplete_fields = ("student", "program")


# StudentApplication admin removed; replaced by the `applications` app.
