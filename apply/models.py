import random
import string

from django.db import models
from django.utils import timezone

from programs.models import Program, RaceEthnicity, School, Student


class ApplicationOTP(models.Model):
    email = models.EmailField()
    code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    is_verified = models.BooleanField(default=False)

    def is_valid(self):
        # OTP is valid for 10 minutes
        return (timezone.now() - self.created_at).total_seconds() < 600

    @classmethod
    def generate_otp(cls, email):
        code = "".join(random.choices(string.digits, k=6))
        return cls.objects.create(email=email, code=code)


class StudentApplication(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("accepted", "Accepted"),
        ("rejected", "Rejected"),
    ]

    program = models.ForeignKey(
        Program, on_delete=models.CASCADE, related_name="apply_applications"
    )

    # Student Info
    preferred_first_name = models.CharField(max_length=150)
    legal_first_name = models.CharField(max_length=150, blank=True, null=True)
    last_name = models.CharField(max_length=150)
    date_of_birth = models.DateField()
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=50)
    zip_code = models.CharField(max_length=20)
    phone_number = models.CharField(max_length=30, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)

    school = models.ForeignKey(
        School,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="apply_applications",
    )
    grade = models.PositiveSmallIntegerField(
        help_text="Grade for the program year (K-12)"
    )
    race_ethnicity = models.CharField(max_length=255, blank=True, null=True)
    tshirt_size = models.CharField(max_length=20)
    allergies = models.TextField(blank=True, null=True)
    medical_conditions = models.TextField(blank=True, null=True)

    # Essay Section
    interest_reason = models.TextField(blank=True, null=True)
    hope_to_gain = models.TextField(blank=True, null=True)
    past_impact_student = models.TextField(
        blank=True,
        null=True,
        help_text="What do you feel was your biggest impact to your team in the past?",
    )
    past_impact_team = models.TextField(
        blank=True,
        null=True,
        help_text="What do you feel was the team's biggest impact on you?",
    )

    # Parent 1 Info
    parent1_preferred_first_name = models.CharField(max_length=150)
    parent1_legal_first_name = models.CharField(max_length=150, blank=True, null=True)
    parent1_last_name = models.CharField(max_length=150)
    parent1_phone_number = models.CharField(max_length=30)
    parent1_email = models.EmailField()
    parent1_email_notices = models.BooleanField(default=True)

    # Parent 2 Info
    parent2_preferred_first_name = models.CharField(
        max_length=150, blank=True, null=True
    )
    parent2_legal_first_name = models.CharField(max_length=150, blank=True, null=True)
    parent2_last_name = models.CharField(max_length=150, blank=True, null=True)
    parent2_phone_number = models.CharField(max_length=30, blank=True, null=True)
    parent2_email = models.EmailField(blank=True, null=True)
    parent2_email_notices = models.BooleanField(default=False)

    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="pending", db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Application: {self.preferred_first_name} {self.last_name} → {self.program}"
