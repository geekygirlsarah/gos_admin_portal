from django.db import models
from django.utils import timezone


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
        "programs.Student", on_delete=models.CASCADE, related_name="rfid_cards"
    )
    is_active = models.BooleanField(default=True)
    assigned_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["uid"]

    def __str__(self):
        return f"{self.uid} → {self.student}"


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
    visitor_name = models.CharField(max_length=120, blank=True)
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
        ]
        ordering = ["-occurred_at", "-id"]

    def __str__(self):
        person = self.student or self.visitor_name or self.rfid_uid or "Unknown"
        return f"{self.event_type} {person} @ {self.occurred_at:%Y-%m-%d %H:%M}"


class AttendanceSession(models.Model):
    program = models.ForeignKey("programs.Program", on_delete=models.PROTECT)
    student = models.ForeignKey(
        "programs.Student", on_delete=models.PROTECT, null=True, blank=True
    )
    visitor_name = models.CharField(max_length=120, blank=True)
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
