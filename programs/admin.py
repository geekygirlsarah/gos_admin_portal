from django.contrib import admin
from .models import Program, Enrollment, Student, School, Parent, Mentor, Fee, Payment, SlidingScale


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = ('name', 'year', 'active', 'updated_at')
    search_fields = ('name',)
    list_filter = ('active', 'year')


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
    list_display = ('name',)
    search_fields = ('name',)


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = (
        'first_name', 'preferred_first_name', 'last_name', 'pronouns', 'grade',
        'andrew_id', 'on_discord', 'active', 'updated_at'
    )
    list_filter = ('active', 'on_discord', 'seen_once', 'grade')
    search_fields = (
        'first_name', 'preferred_first_name', 'last_name', 'pronouns', 'user__username', 'user__email',
        'personal_email', 'andrew_id', 'andrew_email', 'discord_handle', 'school__name', 'city', 'state'
    )
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Identity', {
            'fields': (
                'first_name', 'preferred_first_name', 'last_name', 'pronouns', 'date_of_birth',
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
                'school', 'grade',
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

