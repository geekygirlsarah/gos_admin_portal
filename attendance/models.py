from django.db import models
from django.utils import timezone


def _signout_upload_to(instance, filename):
    """Signature images land at MEDIA_ROOT/digital_signouts/<config_id>/<filename>."""
    config_id = (
        instance.config_id if getattr(instance, "config_id", None) else "unknown"
    )
    return f"digital_signouts/{config_id}/{filename}"


class KioskConfig(models.Model):
    """Configuration for a public kiosk sign-in page.

    A kiosk config links a program to a public sign-in page at /kiosk/<id>/.
    Access is controlled via a server-side HttpOnly cookie set when a mentor
    unlocks the kiosk — no API key is stored or sent to the browser.
    """

    label = models.CharField(
        max_length=100,
        help_text="Human-readable name for this kiosk (e.g. 'Build Space Entry').",
    )
    program = models.ForeignKey(
        "programs.Program",
        on_delete=models.PROTECT,
        related_name="kiosk_configs",
        help_text="Program that attendance will be recorded under.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive kiosks return a 404 page.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["label"]
        verbose_name = "Kiosk Configuration"
        verbose_name_plural = "Kiosk Configurations"

    def __str__(self):
        return f"{self.label} ({self.program})"


class KioskDevice(models.Model):
    name = models.CharField(max_length=100)
    program = models.ForeignKey("programs.Program", on_delete=models.PROTECT)
    api_key = models.CharField(max_length=64, unique=True)
    is_active = models.BooleanField(default=True)
    location = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.location})" if self.location else self.name


class RFIDCard(models.Model):
    uid = models.CharField(max_length=64, unique=True)
    student = models.ForeignKey(
        "programs.Student",
        on_delete=models.CASCADE,
        related_name="rfid_cards",
        null=True,
        blank=True,
    )
    adult = models.ForeignKey(
        "programs.Adult",
        on_delete=models.CASCADE,
        related_name="rfid_cards",
        null=True,
        blank=True,
    )
    is_active = models.BooleanField(default=True)
    assigned_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["uid"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(student__isnull=False, adult__isnull=True)
                    | models.Q(student__isnull=True, adult__isnull=False)
                ),
                name="rfid_card_owner_check",
            )
        ]

    def __str__(self):
        owner = self.student or self.adult
        return f"{self.uid} → {owner}"


class AttendanceEvent(models.Model):
    IN = "IN"
    OUT = "OUT"
    AUTO = "AUTO"
    EVENT_CHOICES = [
        (IN, "In"),
        (OUT, "Out"),
        (AUTO, "Auto"),
    ]

    program = models.ForeignKey("programs.Program", on_delete=models.PROTECT)
    student = models.ForeignKey(
        "programs.Student", on_delete=models.PROTECT, null=True, blank=True
    )
    adult = models.ForeignKey(
        "programs.Adult", on_delete=models.PROTECT, null=True, blank=True
    )
    visitor_name = models.CharField(max_length=120, blank=True)
    visitor_team_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="FRC/FTC/FLL team number for visiting teams.",
    )
    rfid_uid = models.CharField(max_length=64, blank=True)
    kiosk = models.ForeignKey(
        KioskDevice, on_delete=models.SET_NULL, null=True, blank=True
    )
    event_type = models.CharField(max_length=4, choices=EVENT_CHOICES)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    source = models.CharField(max_length=40, default="kiosk")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["program", "student", "occurred_at"]),
            models.Index(fields=["program", "adult", "occurred_at"]),
        ]
        ordering = ["-occurred_at", "-id"]

    def __str__(self):
        person = (
            self.student
            or self.adult
            or self.visitor_name
            or self.rfid_uid
            or "Unknown"
        )
        return f"{self.event_type} {person} @ {self.occurred_at:%Y-%m-%d %H:%M}"


class AttendanceSession(models.Model):
    program = models.ForeignKey("programs.Program", on_delete=models.PROTECT)
    student = models.ForeignKey(
        "programs.Student", on_delete=models.PROTECT, null=True, blank=True
    )
    adult = models.ForeignKey(
        "programs.Adult", on_delete=models.PROTECT, null=True, blank=True
    )
    visitor_name = models.CharField(max_length=120, blank=True)
    visitor_team_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="FRC/FTC/FLL team number for visiting teams.",
    )
    check_in = models.DateTimeField(db_index=True)
    check_out = models.DateTimeField(null=True, blank=True, db_index=True)
    duration_minutes = models.PositiveIntegerField(default=0)
    opened_by_event = models.ForeignKey(
        AttendanceEvent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    closed_by_event = models.ForeignKey(
        AttendanceEvent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["program", "student", "check_in"]),
            models.Index(fields=["program", "adult", "check_in"]),
            models.Index(
                fields=["program", "student", "check_in"],
                condition=models.Q(check_out__isnull=True),
                name="att_sess_open_student_idx",
            ),
            models.Index(
                fields=["program", "adult", "check_in"],
                condition=models.Q(check_out__isnull=True),
                name="att_sess_open_adult_idx",
            ),
            models.Index(
                fields=["program", "visitor_name", "check_in"],
                name="att_sess_prog_visitor_in_idx",
            ),
        ]
        ordering = ["-check_in"]

    @property
    def is_open(self):
        return self.check_out is None

    def recompute_duration(self):
        if self.check_out and self.check_out > self.check_in:
            delta = self.check_out - self.check_in
            self.duration_minutes = int(delta.total_seconds() // 60)
        else:
            self.duration_minutes = 0

    @property
    def duration_hm(self) -> str:
        """Format duration_minutes as H:MM (e.g., 2:05)."""
        mins = int(self.duration_minutes or 0)
        hours = mins // 60
        rem = mins % 60
        return f"{hours}:{rem:02d}"


class DigitalSignoutConfig(models.Model):
    """Configuration for a public digital sign-out page.

    Mirrors :class:`KioskConfig`: an admin-managed row linking a label to a
    program, which serves a login-exempt page at /signout/<id>/ where parents
    can digitally sign their student out on a tablet.
    """

    label = models.CharField(
        max_length=100,
        help_text="Human-readable name for this sign-out station (e.g. 'Front Door').",
    )
    program = models.ForeignKey(
        "programs.Program",
        on_delete=models.PROTECT,
        related_name="signout_configs",
        help_text="Program whose students can be signed out at this station.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive sign-out stations return a 404 page.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["label"]
        verbose_name = "Digital Sign-out Station"
        verbose_name_plural = "Digital Sign-out Stations"

    def __str__(self):
        return f"{self.label} ({self.program})"


class StudentPresence(models.Model):
    """Permanent per-day present/absent record for a student in a program.

    Unmarked students default to present; an explicit ``absent`` record is
    what hides a student from the digital sign-out picker. Toggling a
    student's status on the same day overwrites the existing row.
    """

    PRESENT = "present"
    ABSENT = "absent"
    STATUS_CHOICES = [
        (PRESENT, "Present"),
        (ABSENT, "Absent"),
    ]

    program = models.ForeignKey(
        "programs.Program", on_delete=models.PROTECT, related_name="student_presences"
    )
    student = models.ForeignKey(
        "programs.Student", on_delete=models.PROTECT, related_name="presences"
    )
    date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES)
    marked_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="marked_student_presences",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("program", "student", "date")
        ordering = ["-date", "student"]

    def __str__(self):
        return f"{self.student} {self.status} @ {self.date}"


class DigitalSignout(models.Model):
    """A single recorded digital sign-out of a student by a parent.

    Stores the student, the parent-entered name, the drawn signature image,
    and when it happened.
    """

    config = models.ForeignKey(
        DigitalSignoutConfig,
        on_delete=models.PROTECT,
        related_name="signouts",
    )
    program = models.ForeignKey(
        "programs.Program", on_delete=models.PROTECT, related_name="digital_signouts"
    )
    student = models.ForeignKey(
        "programs.Student",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="digital_signouts",
    )
    signed_by_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Name the parent/guardian entered.",
    )
    signature = models.FileField(
        upload_to=_signout_upload_to,
        blank=True,
        help_text="PNG image of the drawn signature.",
    )
    signed_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-signed_at", "-id"]
        indexes = [
            models.Index(fields=["program", "student", "signed_at"]),
        ]

    def __str__(self):
        person = self.student or "Unknown student"
        return f"{person} signed out @ {self.signed_at:%Y-%m-%d %H:%M}"
