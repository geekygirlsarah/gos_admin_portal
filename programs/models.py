from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator


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
    year = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        db_index=True,
        validators=[MinValueValidator(1900), MaxValueValidator(2200)],
        help_text="Calendar year the program runs (e.g., 2025).",
    )
    start_date = models.DateField(null=True, blank=True, db_index=True, help_text="Program start date")
    end_date = models.DateField(null=True, blank=True, db_index=True, help_text="Program end date")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class School(models.Model):
    name = models.CharField(max_length=150, unique=True)
    district = models.CharField(max_length=150, blank=True, null=True, verbose_name='School district')
    street_address = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(max_length=50, blank=True, null=True)
    zip_code = models.CharField(max_length=20, blank=True, null=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class RaceEthnicity(models.Model):
    """Canonical race/ethnicity options for Students (multi-select)."""
    key = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100, unique=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name_plural = 'Race/Ethnicity Options'

    def __str__(self):
        return self.name

    @classmethod
    def match_from_text(cls, text: str):
        """Best-effort mapping from a free-text race/ethnicity string to option queryset.
        Matches by keyword; supports comma/semicolon-separated lists.
        """
        if not text:
            return cls.objects.none()
        import re
        s = (text or '').lower()
        # Split on common separators to get tokens too
        parts = [p.strip() for p in re.split(r'[;,/\\]|\band\b', s) if p.strip()]
        hay = ' ' + s + ' '
        keys = set()
        def has(substr):
            return substr in hay
        # American Indian or Alaska Native
        if any('american indian' in p or 'alaska' in p or 'native american' in p for p in parts) or has('american indian') or has('alaska') or has('native american'):
            keys.add('american-indian-or-alaska-native')
        # Asian
        if has(' asian'):
            keys.add('asian')
        # Black or African-American
        if has('black') or has('african-american') or has('african american'):
            keys.add('black-or-african-american')
        # Hispanic or Latino
        if has('hispanic') or has('latino') or has('latina') or has('latinx'):
            keys.add('hispanic-or-latino')
        # Middle Eastern or North African
        if has('middle eastern') or has('north african') or has('mena'):
            keys.add('middle-eastern-or-north-african')
        # Native Hawaiian or Other Pacific Islander
        if has('hawaiian') or has('pacific islander'):
            keys.add('native-hawaiian-or-other-pacific-islander')
        # White
        if has(' white'):
            keys.add('white')
        # Other
        if has('other') or (not keys and s.strip()):
            # If text provided but no match, classify as other
            keys.add('other')
        return cls.objects.filter(key__in=keys)


class Student(models.Model):
    def save(self, *args, **kwargs):
        # Auto-opt-in primary contact for email updates if assigned
        try:
            parent = self.primary_contact
        except Exception:
            parent = None
        if parent and parent.email_updates is False:
            parent.email_updates = True
            parent.save(update_fields=['email_updates'])
        super().save(*args, **kwargs)

    def eighteenth_birthday(self):
        """Return the date this student turns 18, or None if DOB unknown."""
        dob = self.date_of_birth
        if not dob:
            return None
        try:
            return dob.replace(year=dob.year + 18)
        except ValueError:
            # Handle Feb 29 on non-leap years by using Feb 28
            return dob.replace(month=2, day=28, year=dob.year + 18)

    def requires_background_check(self, program: 'Program') -> bool:
        """Whether the student will be 18 at any point during the given program's dates.
        Returns False if insufficient data (no DOB or no program dates).
        """
        if not program or not program.start_date or not program.end_date:
            return False
        b18 = self.eighteenth_birthday()
        if not b18:
            return False
        # If the program end is on/after 18th birthday, and the start is on/before end.
        return program.end_date >= b18 and program.start_date <= program.end_date

    # Optional link to a User so students can self-manage later if desired
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='student_profile',
    )
    legal_first_name = models.CharField(max_length=150, verbose_name='Legal first name')
    first_name = models.CharField(max_length=150, blank=True, null=True, verbose_name='First name')
    last_name = models.CharField(max_length=150)
    pronouns = models.CharField(max_length=50, blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)
    # Background clearances (per-student, separate from mentor clearances)
    has_passed_clearances = models.BooleanField(default=False, help_text='Check if the student has completed and passed required background clearances.')
    clearances_expiration_date = models.DateField(blank=True, null=True, help_text='Expiration date for the student\'s background clearances (if applicable).')
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
    graduation_year = models.PositiveSmallIntegerField(blank=True, null=True, help_text='Expected high school graduation year')

    # New multi-select of canonical options
    race_ethnicities = models.ManyToManyField('RaceEthnicity', related_name='students', blank=True, verbose_name='Race / Ethnicity')
    tshirt_size = models.CharField(max_length=10, blank=True, null=True)

    seen_once = models.BooleanField(default=False)
    on_discord = models.BooleanField(default=False)
    discord_handle = models.CharField(max_length=100, blank=True, null=True)

    # Health & Medical
    allergies = models.TextField(blank=True, null=True, help_text='List any food, drug, environmental, or other allergies. Include severity and typical reactions if known.')
    dietary_restrictions = models.TextField(blank=True, null=True, help_text='Dietary needs or restrictions (e.g., vegetarian, halal, no pork, no nuts).')
    medical_notes = models.TextField(blank=True, null=True, help_text='Other health information staff should know (e.g., asthma, seizures, physical limitations).')

    # FIRST Website
    first_has_account = models.BooleanField(default=False, verbose_name='Has FIRST account')
    first_attached_to_parent_account = models.BooleanField(default=False, verbose_name='Attached to parent account')
    first_signed_cr = models.BooleanField(default=False, verbose_name='Signed FIRST Consent & Release (C&R)')
    first_registered_teams = models.CharField(max_length=200, blank=True, null=True, verbose_name='Registered team(s)', help_text='Team numbers or names, comma-separated')

    # New contact fields
    primary_contact = models.ForeignKey('Parent', on_delete=models.SET_NULL, null=True, blank=True, related_name='primary_for_students')
    secondary_contact = models.ForeignKey('Parent', on_delete=models.SET_NULL, null=True, blank=True, related_name='secondary_for_students')

    active = models.BooleanField(default=True)
    programs = models.ManyToManyField('Program', through='Enrollment', related_name='students', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['first_name', 'last_name']

    def __str__(self):
        pref = self.first_name or self.legal_first_name
        full = f"{pref} {self.last_name}".strip()
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
    email_updates = models.BooleanField(default=False, help_text='If checked, this parent/guardian will receive email updates.')

    students = models.ManyToManyField(Student, related_name='parents', blank=True)

    class Meta:
        ordering = ['first_name', 'last_name']

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
    # Editable date for when the fee is considered received/posted
    date = models.DateField(blank=True, null=True, help_text='Date the fee was posted/received (used for balance sheet sorting).')
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
    paid_on = models.DateField()
    paid_via = models.CharField(max_length=20, choices=PAID_VIA_CHOICES, default='cash')
    check_number = models.PositiveIntegerField(blank=True, null=True)
    camp_hours = models.PositiveIntegerField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-paid_on', '-created_at']

    def __str__(self):
        return f"Payment ${self.amount} by {self.student} for {self.fee.name} on {self.paid_on}"

    def clean(self):
        # Ensure the payment fee belongs to a program the student is enrolled in
        from django.core.exceptions import ValidationError
        program = self.fee.program if self.fee_id else None
        if program and not Enrollment.objects.filter(student=self.student, program=program).exists():
            raise ValidationError('Student must be enrolled in the program for the selected fee.')

        # If the fee has explicit assignments, the student must be among them
        if self.fee_id and self.fee.assignments.exists():
            if not self.fee.assignments.filter(student=self.student).exists():
                raise ValidationError('This fee is only assigned to specific students, and this student is not assigned.')


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


class FeeAssignment(models.Model):
    """
    Links a Fee to specific students (within the Fee's program).
    If a Fee has any assignments, it applies ONLY to those students.
    """
    fee = models.ForeignKey('Fee', on_delete=models.CASCADE, related_name='assignments')
    student = models.ForeignKey('Student', on_delete=models.CASCADE, related_name='fee_assignments')

    # Optional note
    notes = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('fee', 'student')
        ordering = ['fee__program__name', 'fee__name', 'student__last_name', 'student__first_name']

    def __str__(self):
        return f"{self.fee.name} → {self.student}"

    def clean(self):
        # Ensure the student is enrolled in the same program as the fee
        from django.core.exceptions import ValidationError
        program = self.fee.program if self.fee_id else None
        if program and not Enrollment.objects.filter(student=self.student, program=program).exists():
            raise ValidationError('Assigned student must be enrolled in the fee’s program.')


class Alumni(models.Model):
    """Alumni profile linked to a Student, storing post-graduation details."""
    student = models.OneToOneField('Student', on_delete=models.CASCADE, related_name='alumni_profile')

    # Contact after graduation
    alumni_email = models.EmailField(blank=True, null=True, help_text='Preferred contact email after graduation')
    phone_number = models.CharField(max_length=30, blank=True, null=True)

    # Optional post-grad info
    college = models.CharField(max_length=200, blank=True, null=True)
    field_of_study = models.CharField(max_length=200, blank=True, null=True)
    employer = models.CharField(max_length=200, blank=True, null=True)
    job_title = models.CharField(max_length=200, blank=True, null=True)

    # Consent/preferences
    ok_to_contact = models.BooleanField(default=True, help_text='Alumni consents to be contacted about news/opportunities')

    notes = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['student__last_name', 'student__first_name']
        verbose_name_plural = 'Alumni'

    def __str__(self):
        return f"Alumni: {self.student}"


class StudentApplication(models.Model):
    """Public application submitted by a prospective student to a Program."""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
    ]

    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name='applications')

    # Mirror key Student fields (omit internal/user/parents/media)
    legal_first_name = models.CharField(max_length=150)
    first_name = models.CharField(max_length=150, blank=True, null=True)
    last_name = models.CharField(max_length=150)
    pronouns = models.CharField(max_length=50, blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)

    address = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(max_length=50, blank=True, null=True)
    zip_code = models.CharField(max_length=20, blank=True, null=True)

    cell_phone_number = models.CharField(max_length=30, blank=True, null=True)
    personal_email = models.EmailField(blank=True, null=True)

    andrew_id = models.CharField(max_length=50, blank=True, null=True)
    andrew_email = models.EmailField(blank=True, null=True)

    school = models.ForeignKey('School', on_delete=models.SET_NULL, null=True, blank=True, related_name='applications')
    graduation_year = models.PositiveSmallIntegerField(blank=True, null=True)

    race_ethnicity = models.CharField(max_length=100, blank=True, null=True)
    tshirt_size = models.CharField(max_length=10, blank=True, null=True)

    on_discord = models.BooleanField(default=False)
    discord_handle = models.CharField(max_length=100, blank=True, null=True)

    # Simple parent/guardian contact captured as text for application
    parent_name = models.CharField(max_length=200, blank=True, null=True)
    parent_email = models.EmailField(blank=True, null=True)
    parent_phone = models.CharField(max_length=30, blank=True, null=True)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', db_index=True)
    notes = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Application: {self.first_name or self.legal_first_name} {self.last_name} → {self.program} ({self.status})"

    def approve(self) -> Student:
        """Create (or find) a Student from this application and enroll them in the program."""
        # Try to find existing student by name and email
        student = None
        if self.personal_email:
            student = Student.objects.filter(personal_email__iexact=self.personal_email).first()
        if not student:
            student = Student.objects.create(
                legal_first_name=self.legal_first_name,
                first_name=self.first_name,
                last_name=self.last_name,
                pronouns=self.pronouns,
                date_of_birth=self.date_of_birth,
                address=self.address,
                city=self.city,
                state=self.state,
                zip_code=self.zip_code,
                cell_phone_number=self.cell_phone_number,
                personal_email=self.personal_email,
                andrew_id=self.andrew_id,
                andrew_email=self.andrew_email,
                school=self.school,
                graduation_year=self.graduation_year,
                tshirt_size=self.tshirt_size,
                on_discord=self.on_discord,
                discord_handle=self.discord_handle,
            )
        # Map application race text to Student multi-select
        try:
            options = RaceEthnicity.match_from_text(self.race_ethnicity)
            if options.exists():
                student.race_ethnicities.set(list(options))
        except Exception:
            pass
        # Enroll in program
        Enrollment.objects.get_or_create(student=student, program=self.program)
        # Update status
        if self.status != 'accepted':
            self.status = 'accepted'
            self.save(update_fields=['status', 'updated_at'])
        return student
