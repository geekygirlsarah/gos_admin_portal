from django.contrib import admin

from programs.models import Adult, Enrollment, Student

from .models import ApplicationOTP, StudentApplication


@admin.register(ApplicationOTP)
class ApplicationOTPAdmin(admin.ModelAdmin):
    list_display = ("email", "code", "created_at", "is_verified")
    search_fields = ("email", "code")


@admin.register(StudentApplication)
class StudentApplicationAdmin(admin.ModelAdmin):
    list_display = (
        "last_name",
        "preferred_first_name",
        "program",
        "status",
        "created_at",
    )
    list_filter = ("program", "status")
    search_fields = (
        "preferred_first_name",
        "legal_first_name",
        "last_name",
        "email",
        "parent1_email",
        "parent2_email",
    )
    actions = ["approve_applications", "mark_rejected"]

    def approve_applications(self, request, queryset):
        approved = 0
        for app in queryset:
            # Logic to convert this to programs.Student and programs.Adults

            # 1. Create/Find Student
            student, created = Student.objects.get_or_create(
                legal_first_name=app.legal_first_name or app.preferred_first_name,
                last_name=app.last_name,
                date_of_birth=app.date_of_birth,
                defaults={
                    "first_name": app.preferred_first_name,
                    "address": app.address,
                    "city": app.city,
                    "state": app.state,
                    "zip_code": app.zip_code,
                    "cell_phone_number": app.phone_number,
                    "personal_email": app.email,
                    "school": app.school,
                    "graduation_year": 2030,  # Should calculate from grade
                    "tshirt_size": app.tshirt_size,
                    "medical_info": f"Allergies: {app.allergies}\nMedical: {app.medical_conditions}",
                },
            )

            # 2. Create/Find Parent 1
            parent1, p1_created = Adult.objects.get_or_create(
                email=app.parent1_email,
                defaults={
                    "legal_first_name": app.parent1_legal_first_name
                    or app.parent1_preferred_first_name,
                    "preferred_name": app.parent1_preferred_first_name,
                    "last_name": app.parent1_last_name,
                    "phone_number": app.parent1_phone_number,
                    "email_updates": app.parent1_email_notices,
                    "is_parent": True,
                },
            )
            parent1.students.add(student)

            # 3. Create/Find Parent 2
            if app.parent2_email:
                parent2, p2_created = Adult.objects.get_or_create(
                    email=app.parent2_email,
                    defaults={
                        "legal_first_name": app.parent2_legal_first_name
                        or app.parent2_preferred_first_name,
                        "preferred_name": app.parent2_preferred_first_name,
                        "last_name": app.parent2_last_name,
                        "phone_number": app.parent2_phone_number,
                        "email_updates": app.parent2_email_notices,
                        "is_parent": True,
                    },
                )
                parent2.students.add(student)

            # 4. Enroll
            Enrollment.objects.get_or_create(student=student, program=app.program)

            app.status = "accepted"
            app.save()
            approved += 1

        self.message_user(request, f"Successfully approved {approved} applications.")

    def mark_rejected(self, request, queryset):
        queryset.update(status="rejected")
        self.message_user(request, "Selected applications marked as rejected.")
