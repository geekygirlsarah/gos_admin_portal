from django.contrib import admin
from .models import Program, Enrollment, Student, School, Parent, Mentor, Fee, Payment, SlidingScale, Alumni, StudentApplication


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = ('name', 'year', 'start_date', 'end_date', 'active', 'updated_at')
    search_fields = ('name',)
    list_filter = ('active', 'year', 'start_date', 'end_date')


@admin.register(Fee)
class FeeAdmin(admin.ModelAdmin):
    list_display = ('program', 'name', 'amount', 'date', 'updated_at')
    list_filter = ('program', 'date')
    search_fields = ('name', 'program__name')


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('student', 'fee', 'amount', 'paid_at', 'created_at')
    list_filter = ('fee__program', 'paid_at')
    search_fields = ('student__first_name', 'student__last_name', 'fee__name', 'fee__program__name')
    autocomplete_fields = ('student', 'fee')

class EnrollmentInline(admin.TabularInline):
    model = Enrollment
    extra = 1


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ('name', 'district', 'street_address', 'city', 'state', 'zip_code')
    search_fields = ('name', 'district', 'street_address', 'city', 'state', 'zip_code')


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = (
        'first_name', 'legal_first_name', 'last_name', 'pronouns', 'graduation_year',
        'andrew_id', 'on_discord', 'active', 'updated_at'
    )
    list_filter = ('active', 'on_discord', 'seen_once', 'graduation_year')
    search_fields = (
        'first_name', 'legal_first_name', 'last_name', 'pronouns', 'user__username', 'user__email',
        'personal_email', 'andrew_id', 'andrew_email', 'discord_handle', 'school__name', 'city', 'state'
    )
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Identity', {
            'fields': (
                'first_name', 'legal_first_name', 'last_name', 'pronouns', 'date_of_birth',
            )
        }),
        ('Contact', {
            'fields': (
                'address', 'city', 'state', 'zip_code', 'cell_phone_number', 'personal_email',
            )
        }),
        ('Andrew', {
            'fields': (
                'andrew_id', 'andrew_email',
            )
        }),
        ('School', {
            'fields': (
                'school', 'graduation_year',
            )
        }),
        ('Other', {
            'fields': (
                'race_ethnicity', 'tshirt_size', 'seen_once', 'on_discord', 'discord_handle',
            )
        }),
        ('System', {
            'fields': (
                'active', 'created_at', 'updated_at', 'user',
            )
        }),
    )
    inlines = [EnrollmentInline]

    actions = ['convert_to_alumni', 'remove_alumni_record']

    def convert_to_alumni(self, request, queryset):
        """Create Alumni records for selected students if missing, and deactivate them."""
        from django.utils import timezone
        from .models import Alumni
        created = 0
        updated = 0
        for student in queryset:
            alumni, was_created = Alumni.objects.get_or_create(student=student)
            if was_created:
                created += 1
            else:
                updated += 1
            # Deactivate student as part of conversion convention
            if student.active:
                student.active = False
                student.save(update_fields=['active', 'updated_at'])
        self.message_user(request, f"Alumni created: {created}, existing left unchanged: {updated}. Selected students deactivated.")
    convert_to_alumni.short_description = "Convert to Alumni (create Alumni record and deactivate)"

    def remove_alumni_record(self, request, queryset):
        """Delete Alumni records for selected students (undo), without changing Student.active."""
        from .models import Alumni
        removed = 0
        for student in queryset:
            try:
                student.alumni_profile.delete()
                removed += 1
            except Alumni.DoesNotExist:
                pass
        self.message_user(request, f"Alumni records removed: {removed}.")
    remove_alumni_record.short_description = "Remove Alumni record (undo)"


@admin.register(Parent)
class ParentAdmin(admin.ModelAdmin):
    list_display = ('first_name', 'preferred_first_name', 'last_name', 'relationship_to_student', 'email', 'phone_number')
    search_fields = ('first_name', 'preferred_first_name', 'last_name', 'email', 'phone_number')
    filter_horizontal = ('students',)


@admin.register(Mentor)
class MentorAdmin(admin.ModelAdmin):
    list_display = (
        'first_name', 'preferred_first_name', 'last_name', 'role', 'start_year',
        'andrew_id', 'on_discord', 'active', 'updated_at'
    )
    list_filter = (
        'active', 'role', 'on_discord', 'has_cmu_id_card', 'has_cmu_building_access',
        'has_google_team_drive_access', 'has_google_mentor_drive_access', 'has_google_admin_drive_access',
        'on_first_website', 'signed_first_consent_form', 'on_canvas', 'has_zoom_account', 'in_onshape_classroom',
        'on_canva', 'on_google_mentor_group', 'on_google_field_crew_group',
        'has_paca_clearance', 'has_patch_clearance', 'has_fbi_clearance'
    )
    search_fields = (
        'first_name', 'preferred_first_name', 'last_name', 'pronouns', 'user__username', 'user__email',
        'personal_email', 'andrew_id', 'andrew_email', 'discord_username'
    )
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Identity', {
            'fields': (
                'first_name', 'preferred_first_name', 'last_name', 'pronouns', 'start_year', 'role',
            )
        }),
        ('Contact', {
            'fields': (
                'cell_phone', 'home_phone', 'personal_email',
            )
        }),
        ('Andrew', {
            'fields': (
                'andrew_id', 'andrew_email', 'andrew_id_expiration', 'andrew_id_sponsor',
            )
        }),
        ('Discord', {
            'fields': (
                'on_discord', 'discord_username',
            )
        }),
        ('Access', {
            'fields': (
                'has_cmu_id_card', 'has_cmu_building_access',
                'has_google_team_drive_access', 'has_google_mentor_drive_access', 'has_google_admin_drive_access',
            )
        }),
        ('Participation', {
            'fields': (
                'on_first_website', 'signed_first_consent_form', 'on_canvas', 'has_zoom_account', 'in_onshape_classroom',
                'on_canva', 'on_google_mentor_group', 'on_google_field_crew_group',
            )
        }),
        ('Clearances', {
            'fields': (
                'has_paca_clearance', 'has_patch_clearance', 'has_fbi_clearance', 'pa_clearances_expiration_date',
            )
        }),
        ('Emergency', {
            'fields': (
                'emergency_contact_name', 'emergency_contact_phone',
            )
        }),
        ('System', {
            'fields': (
                'active', 'created_at', 'updated_at', 'user',
            )
        }),
    )

@admin.register(SlidingScale)
class SlidingScaleAdmin(admin.ModelAdmin):
    list_display = ('student', 'program', 'percent', 'is_pending', 'updated_at')
    list_filter = ('program', 'is_pending')
    search_fields = ('student__first_name', 'student__last_name', 'program__name')
    autocomplete_fields = ('student', 'program')


@admin.register(StudentApplication)
class StudentApplicationAdmin(admin.ModelAdmin):
    list_display = ('last_name', 'first_name', 'program', 'personal_email', 'status', 'created_at')
    list_filter = ('program', 'status')
    search_fields = ('first_name', 'legal_first_name', 'last_name', 'personal_email', 'andrew_email')
    readonly_fields = ('created_at', 'updated_at')
    actions = ['approve_applications', 'mark_rejected']

    def approve_applications(self, request, queryset):
        created = 0
        enrolled = 0
        for app in queryset:
            student = app.approve()
            if student:
                enrolled += 1
        self.message_user(request, f"Approved {queryset.count()} application(s). Enrolled {enrolled} student(s).")
    approve_applications.short_description = "Approve selected applications (create Student and enroll)"

    def mark_rejected(self, request, queryset):
        updated = queryset.update(status='rejected')
        self.message_user(request, f"Marked {updated} application(s) as rejected.")
    mark_rejected.short_description = "Mark selected applications as rejected"


@admin.register(Alumni)
class AlumniAdmin(admin.ModelAdmin):
    list_display = (
        'student', 'alumni_email', 'phone_number', 'college', 'field_of_study', 'employer', 'job_title', 'ok_to_contact', 'updated_at'
    )
    list_filter = ('ok_to_contact',)
    search_fields = (
        'student__first_name', 'student__last_name', 'alumni_email', 'college', 'employer', 'job_title'
    )
    autocomplete_fields = ('student',)
    readonly_fields = ('created_at', 'updated_at')

