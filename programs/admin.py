from django.contrib import admin

from .forms import StudentForm
from .models import (
    Adult,
    Enrollment,
    Fee,
    Payment,
    Program,
    ProgramFeature,
    RolePermission,
    School,
    SlidingScale,
    Student,
    StudentApplication,
)


@admin.register(ProgramFeature)
class ProgramFeatureAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "display_order")
    search_fields = ("name", "key")


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display = ("role", "section", "can_read", "can_write")
    list_filter = ("role", "section")
    list_editable = ("can_read", "can_write")


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = ("name", "year", "start_date", "end_date", "active", "updated_at")
    search_fields = ("name",)
    list_filter = ("active", "year", "start_date", "end_date")
    filter_horizontal = ("features",)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "description",
                    "active",
                    "year",
                    "start_date",
                    "end_date",
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
        "fee",
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
        "fee__name",
    )
    autocomplete_fields = ("student", "fee", "program")


class EnrollmentInline(admin.TabularInline):
    model = Enrollment
    extra = 1


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
        "active",
        "graduated",
        "updated_at",
    )
    list_filter = ("active", "graduated", "on_discord", "seen_once", "graduation_year")
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

    def _find_matching_adult(self, s: Student):
        emails = [s.personal_email, s.andrew_email]
        for e in emails:
            if e:
                a = Adult.objects.filter(alumni_email__iexact=e).first()
                if a:
                    return a
                a = Adult.objects.filter(email__iexact=e, is_alumni=True).first()
                if a:
                    return a
        first = (s.first_name or s.legal_first_name or "").strip()
        last = (s.last_name or "").strip()
        if first and last:
            return Adult.objects.filter(
                first_name__iexact=first, last_name__iexact=last, is_alumni=True
            ).first()
        return None

    def convert_to_alumni(self, request, queryset):
        """Create or update Adult records flagged as alumni for selected students, and mark the students as graduated (no change to active)."""
        created = 0
        existed = 0
        for student in queryset:
            adult = self._find_matching_adult(student)
            if not adult:
                Adult.objects.create(
                    first_name=student.first_name or student.legal_first_name or "",
                    last_name=student.last_name or "",
                    alumni_email=student.personal_email or student.andrew_email,
                    is_alumni=True,
                )
                created += 1
            else:
                changed = False
                if not adult.is_alumni:
                    adult.is_alumni = True
                    changed = True
                if not adult.alumni_email and (
                    student.personal_email or student.andrew_email
                ):
                    adult.alumni_email = student.personal_email or student.andrew_email
                    changed = True
                if changed:
                    adult.save(
                        update_fields=["is_alumni", "alumni_email", "updated_at"]
                    )
                existed += 1
            if not student.graduated:
                student.graduated = True
                student.save(update_fields=["graduated", "updated_at"])
        self.message_user(
            request,
            f"Adults created as Alumni: {created}, existing/updated: {existed}. Selected students marked as graduated.",
        )

    convert_to_alumni.short_description = (
        "Convert to Alumni (mark/create Adult and mark students graduated)"
    )

    def remove_alumni_flag(self, request, queryset):
        """Unset the is_alumni flag on matching Adult records for selected students (undo)."""
        unset = 0
        for student in queryset:
            adult = self._find_matching_adult(student)
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
        "relationship_to_student",
        "is_parent",
        "is_mentor",
        "is_alumni",
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
    filter_horizontal = ("students",)


@admin.register(SlidingScale)
class SlidingScaleAdmin(admin.ModelAdmin):
    list_display = ("student", "program", "percent", "date", "is_pending", "updated_at")
    list_filter = ("program", "is_pending")
    search_fields = ("student__first_name", "student__last_name", "program__name")
    autocomplete_fields = ("student", "program")


@admin.register(StudentApplication)
class StudentApplicationAdmin(admin.ModelAdmin):
    list_display = (
        "last_name",
        "first_name",
        "program",
        "personal_email",
        "status",
        "created_at",
    )
    list_filter = ("program", "status")
    search_fields = (
        "first_name",
        "legal_first_name",
        "last_name",
        "personal_email",
        "andrew_email",
    )
    readonly_fields = ("created_at", "updated_at")
    actions = ["approve_applications", "mark_rejected"]

    def approve_applications(self, request, queryset):
        created = 0
        enrolled = 0
        for app in queryset:
            student = app.approve()
            if student:
                enrolled += 1
        self.message_user(
            request,
            f"Approved {queryset.count()} application(s). Enrolled {enrolled} student(s).",
        )

    approve_applications.short_description = (
        "Approve selected applications (create Student and enroll)"
    )

    def mark_rejected(self, request, queryset):
        updated = queryset.update(status="rejected")
        self.message_user(request, f"Marked {updated} application(s) as rejected.")

    mark_rejected.short_description = "Mark selected applications as rejected"
