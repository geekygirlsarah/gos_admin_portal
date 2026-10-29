from django.db import models


class RolePermission(models.Model):
    """
    Dynamic permission settings for Mentors and Parents.
    Lead Mentors can customize read/write access for each role to each section.
    """

    SECTION_CHOICES = [
        ("student_info", "Student - Info (General)"),
        ("identity", "Student - Identity"),
        ("contact_address", "Student - Contact & Address"),
        ("health_medical", "Student - Health & Medical"),
        ("school", "Student - School"),
        ("cmu_andrew", "Student - CMU Andrew ID"),
        ("background_checks", "Student - Background Checks"),
        ("student_documents", "Student - Signed Documents"),
        ("discord", "Student - Discord"),
        ("first_website", "Student - FIRST Website"),
        ("parents_emergency", "Student - Parents/Emergency Contacts"),
        ("other_details", "Student - Other Details"),
        ("attendance", "Student - Attendance"),
        ("adult_info", "Adult - Info"),
        ("payments", "Payments - General"),
        ("sliding_scale", "Payments - Sliding Scale"),
        ("fees", "Programs - Fees"),
        ("programs", "Programs - General"),
        ("team_assignments", "Programs - Team Assignments"),
        ("badge_award", "Badges - Award"),
        ("badge_manage", "Badges - Create / Manage"),
        ("orders-view", "Orders - See requested orders"),
        ("orders-request", "Orders - Place item request"),
        ("orders-manage", "Orders - Place new orders"),
        ("orders-shipping", "Orders - Add shipping"),
        ("calendar", "Calendar"),
    ]
    ROLE_CHOICES = [
        ("Mentor", "Mentor"),
        ("Parent", "Parent"),
        ("Student", "Student"),
        ("Alumni", "Alumni"),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    section = models.CharField(max_length=50, choices=SECTION_CHOICES)
    can_read = models.BooleanField(default=True)
    can_write = models.BooleanField(default=False)

    class Meta:
        unique_together = ("role", "section")
        verbose_name = "Role Permission"
        verbose_name_plural = "Role Permissions"

    def __str__(self):
        return f"{self.role} - {self.get_section_display()} (R:{self.can_read}, W:{self.can_write})"
