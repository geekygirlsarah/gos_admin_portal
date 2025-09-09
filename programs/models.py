from django.conf import settings
from django.db import models


RELATIONSHIP_CHOICES = [
    ('parent', 'Parent'),
    ('mother', 'Mother'),
    ('father', 'Father'),
    ('grandparent', 'Grandparent'),
    ('grandmother', 'Grandmother'),
    ('grandfather', 'Grandfather'),
    ('pibling', 'Pibling'),
    ('aunt', 'Aunt'),
    ('uncle', 'Uncle'),
    ('sibling', 'Sibling'),
    ('sister', 'Sister'),
    ('brother', 'Brother'),
    ('friend', 'Friend'),
    ('guardian', 'Guardian'),
    ('other', 'Other'),
]

MENTOR_ROLE_CHOICES = [
    ('mentor', 'Mentor'),
    ('volunteer', 'Volunteer'),
    ('chaperone', 'Chaperone'),
]


class Program(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class School(models.Model):
    name = models.CharField(max_length=150, unique=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Student(models.Model):
    # Optional link to a User so students can self-manage later if desired
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='student_profile',
    )
    first_name = models.CharField(max_length=150)
    preferred_first_name = models.CharField(max_length=150, blank=True, null=True)
    last_name = models.CharField(max_length=150)
    pronouns = models.CharField(max_length=50, blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)
    photo = models.ImageField(upload_to='photos/students/', blank=True, null=True)

    address = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(max_length=50, blank=True, null=True)
    zip_code = models.CharField(max_length=20, blank=True, null=True)

    cell_phone_number = models.CharField(max_length=30, blank=True, null=True)
    personal_email = models.EmailField(blank=True, null=True)

    andrew_id = models.CharField(max_length=50, blank=True, null=True)
    andrew_email = models.EmailField(blank=True, null=True)

    school = models.ForeignKey('School', on_delete=models.SET_NULL, null=True, blank=True, related_name='students')
    grade = models.PositiveSmallIntegerField(blank=True, null=True)

    race_ethnicity = models.CharField(max_length=100, blank=True, null=True)
    tshirt_size = models.CharField(max_length=10, blank=True, null=True)

    seen_once = models.BooleanField(default=False)
    on_discord = models.BooleanField(default=False)
    discord_handle = models.CharField(max_length=100, blank=True, null=True)

    # New contact fields
    primary_contact = models.ForeignKey('Parent', on_delete=models.SET_NULL, null=True, blank=True, related_name='primary_for_students')
    secondary_contact = models.ForeignKey('Parent', on_delete=models.SET_NULL, null=True, blank=True, related_name='secondary_for_students')

    active = models.BooleanField(default=True)
    programs = models.ManyToManyField('Program', through='Enrollment', related_name='students', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['last_name', 'first_name']

    def __str__(self):
        full = f"{self.first_name} {self.last_name}".strip()
        return full or f"Student #{self.pk}"


class Enrollment(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    program = models.ForeignKey(Program, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('student', 'program')
        verbose_name = 'Enrollment'
        verbose_name_plural = 'Enrollments'

    def __str__(self):
        return f'{self.student} → {self.program}'


class Parent(models.Model):
    first_name = models.CharField(max_length=150)
    preferred_first_name = models.CharField(max_length=150, blank=True, null=True)
    last_name = models.CharField(max_length=150)
    relationship_to_student = models.CharField(max_length=20, choices=RELATIONSHIP_CHOICES, default='parent')
    email = models.EmailField(blank=True, null=True)
    phone_number = models.CharField(max_length=30, blank=True, null=True)

    students = models.ManyToManyField(Student, related_name='parents', blank=True)

    class Meta:
        ordering = ['last_name', 'first_name']

    def __str__(self):
        pref = self.preferred_first_name or self.first_name
        return f"{pref} {self.last_name}".strip()


class Mentor(models.Model):
    # Optional link to a User; when set we will associate the user with the Mentor role group via signal
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='mentor_profile',
    )

    # Identity
    first_name = models.CharField(max_length=150)
    preferred_first_name = models.CharField(max_length=150, blank=True, null=True)
    last_name = models.CharField(max_length=150)
    pronouns = models.CharField(max_length=50, blank=True, null=True)
    start_year = models.PositiveSmallIntegerField(blank=True, null=True)
    role = models.CharField(max_length=20, choices=MENTOR_ROLE_CHOICES, default='mentor')
    photo = models.ImageField(upload_to='photos/mentors/', blank=True, null=True)

    # Contact
    cell_phone = models.CharField(max_length=30, blank=True, null=True)
    home_phone = models.CharField(max_length=30, blank=True, null=True)
    personal_email = models.EmailField(blank=True, null=True)

    # Andrew ID details
    andrew_id = models.CharField(max_length=50, blank=True, null=True)
    andrew_email = models.EmailField(blank=True, null=True)
    andrew_id_expiration = models.DateField(blank=True, null=True)
    andrew_id_sponsor = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='sponsored_andrew_ids')

    # Discord
    on_discord = models.BooleanField(default=False)
    discord_username = models.CharField(max_length=100, blank=True, null=True)

    # CMU access
    has_cmu_id_card = models.BooleanField(default=False)
    has_cmu_building_access = models.BooleanField(default=False)

    # Google access
    has_google_team_drive_access = models.BooleanField(default=False)
    has_google_mentor_drive_access = models.BooleanField(default=False)
    has_google_admin_drive_access = models.BooleanField(default=False)

    # Online platforms / memberships
    on_first_website = models.BooleanField(default=False)
    signed_first_consent_form = models.BooleanField(default=False)
    on_canvas = models.BooleanField(default=False)
    has_zoom_account = models.BooleanField(default=False)
    in_onshape_classroom = models.BooleanField(default=False)
    on_canva = models.BooleanField(default=False)
    on_google_mentor_group = models.BooleanField(default=False)
    on_google_field_crew_group = models.BooleanField(default=False)

    # Clearances
    has_paca_clearance = models.BooleanField(default=False)
    has_patch_clearance = models.BooleanField(default=False)
    has_fbi_clearance = models.BooleanField(default=False)
    pa_clearances_expiration_date = models.DateField(blank=True, null=True)

    # Emergency contact
    emergency_contact_name = models.CharField(max_length=150, blank=True, null=True)
    emergency_contact_phone = models.CharField(max_length=30, blank=True, null=True)

    # Status
    active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['last_name', 'first_name']

    def __str__(self):
        pref = self.preferred_first_name or self.first_name
        return f"{pref} {self.last_name}".strip()


class Fee(models.Model):
    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name='fees')
    name = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['program__name', 'name']
        unique_together = ('program', 'name')

    def __str__(self):
        return f"{self.program.name} — {self.name}: ${self.amount}"


class Payment(models.Model):
    PAID_VIA_CHOICES = [
        ('check', 'Check'),
        ('credit_card', 'Credit Card'),
        ('cash', 'Cash'),
        ('camp', 'Camp'),
        ('other', 'Other'),
    ]

    student = models.ForeignKey('Student', on_delete=models.CASCADE, related_name='payments')
    fee = models.ForeignKey('Fee', on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    paid_at = models.DateField()
    paid_via = models.CharField(max_length=20, choices=PAID_VIA_CHOICES, default='cash')
    check_number = models.PositiveIntegerField(blank=True, null=True)
    camp_hours = models.PositiveIntegerField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-paid_at', '-created_at']

    def __str__(self):
        return f"Payment ${self.amount} by {self.student} for {self.fee.name} on {self.paid_at}"

    def clean(self):
        # Ensure the payment fee belongs to a program the student is enrolled in
        from django.core.exceptions import ValidationError
        program = self.fee.program if self.fee_id else None
        if program and not Enrollment.objects.filter(student=self.student, program=program).exists():
            raise ValidationError('Student must be enrolled in the program for the selected fee.')


class SlidingScale(models.Model):
    student = models.ForeignKey('Student', on_delete=models.CASCADE, related_name='sliding_scales')
    program = models.ForeignKey('Program', on_delete=models.CASCADE, related_name='sliding_scales')
    percent = models.DecimalField(max_digits=5, decimal_places=2, help_text='Percent discount applied to total program fees (0–100).')
    family_size = models.PositiveIntegerField(blank=True, null=True)
    adjusted_gross_income = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    is_pending = models.BooleanField(default=False)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('student', 'program')
        ordering = ['program__name', 'student__last_name', 'student__first_name']

    def __str__(self):
        return f"Sliding scale {self.percent}% for {self.student} in {self.program}"
