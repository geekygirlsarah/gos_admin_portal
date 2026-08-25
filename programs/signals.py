import logging

from django.apps import apps
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_delete, post_migrate, post_save, pre_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)

ROLE_GROUPS = (
    "LeadMentor",
    "Mentor",
    "Parent",
    "Student",
)


def ensure_group(name: str) -> Group:
    group, _ = Group.objects.get_or_create(name=name)
    return group


def assign_default_permissions():
    Program = apps.get_model("programs", "Program")
    program_ct = ContentType.objects.get_for_model(Program)

    # Student model may not exist on very first migrate run
    try:
        Student = apps.get_model("programs", "Student")
        student_ct = ContentType.objects.get_for_model(Student)
    except Exception:
        Student = None
        student_ct = None

    program_perms = {
        "add_program": Permission.objects.get(
            codename="add_program", content_type=program_ct
        ),
        "change_program": Permission.objects.get(
            codename="change_program", content_type=program_ct
        ),
        "delete_program": Permission.objects.get(
            codename="delete_program", content_type=program_ct
        ),
        "view_program": Permission.objects.get(
            codename="view_program", content_type=program_ct
        ),
    }

    student_perms = {}
    if student_ct:
        student_perms = {
            "add_student": Permission.objects.get(
                codename="add_student", content_type=student_ct
            ),
            "change_student": Permission.objects.get(
                codename="change_student", content_type=student_ct
            ),
            "delete_student": Permission.objects.get(
                codename="delete_student", content_type=student_ct
            ),
            "view_student": Permission.objects.get(
                codename="view_student", content_type=student_ct
            ),
        }

    lead = ensure_group("LeadMentor")
    mentor = ensure_group("Mentor")
    parent = ensure_group("Parent")
    student_group = ensure_group("Student")

    # Lead mentors: all perms. The LeadMentor group also carries the
    # review_application permission (granted by applications migration 0011).
    lead.permissions.add(*program_perms.values())
    if student_perms:
        lead.permissions.add(*student_perms.values())

    # Mentors/Parents: can manage students, view programs
    mentor.permissions.add(program_perms["view_program"])
    parent.permissions.add(program_perms["view_program"])
    if student_perms:
        mentor.permissions.add(
            student_perms["view_student"],
            student_perms["change_student"],
            student_perms["add_student"],
        )
        parent.permissions.add(
            student_perms["view_student"],
            student_perms["change_student"],
            student_perms["add_student"],
        )

    # Students: view programs, view student (object-level restrictions handled in views if needed)
    student_group.permissions.add(program_perms["view_program"])
    if student_perms:
        student_group.permissions.add(student_perms["view_student"])


@receiver(post_migrate)
def create_roles_and_permissions(sender, app_config=None, **kwargs):
    try:
        for name in ROLE_GROUPS:
            ensure_group(name)
        assign_default_permissions()
    except Exception:
        # Avoid breaking migrate due to permissions wiring
        pass  # nosec B110


@receiver(post_save, sender="programs.Adult")
def ensure_user_in_adult_group(sender, instance, created, **kwargs):
    try:
        if instance.user_id:
            if instance.is_mentor:
                group = ensure_group("Mentor")
                instance.user.groups.add(group)
            if instance.is_parent:
                group = ensure_group("Parent")
                instance.user.groups.add(group)
    except Exception:
        logger.debug("Failed to add user to Adult groups", exc_info=True)


@receiver(post_delete, sender="programs.AdultStudentRelationship")
def audit_guardian_removed(sender, instance, **kwargs):
    """Emit GUARDIAN_REMOVED when a guardian/parent link is deleted."""
    from audit.events import AuditEvent
    from audit.service import log_event

    try:
        adult_repr = str(instance.adult) if instance.adult_id else "Unknown"
        student_repr = str(instance.student) if instance.student_id else "Unknown"
        log_event(
            event=AuditEvent.GUARDIAN_REMOVED,
            resource=instance,
            notes=(
                f"Guardian {adult_repr} removed from student {student_repr}. "
                f"Relationship was: {instance.get_relationship_to_student_display()}."
            ),
        )
    except Exception:
        logger.debug("Failed to audit GUARDIAN_REMOVED", exc_info=True)


@receiver(post_save, sender="programs.AdultStudentRelationship")
def mark_adult_as_parent_on_relationship(sender, instance, created, **kwargs):
    """Auto-flag an Adult as a parent whenever a student relationship is made."""
    try:
        if instance.adult_id and not instance.adult.is_parent:
            instance.adult.__class__.objects.filter(pk=instance.adult_id).update(
                is_parent=True
            )
    except Exception:
        logger.debug("Failed to mark Adult as parent", exc_info=True)


@receiver(post_save, sender="programs.Student")
def ensure_user_in_student_group(sender, instance, created, **kwargs):
    try:
        if instance.user_id:
            group = ensure_group("Student")
            instance.user.groups.add(group)
    except Exception:
        logger.debug("Failed to add user to Student group", exc_info=True)


@receiver(post_save, sender="programs.Fee")
def notify_parents_on_fee_added(sender, instance, created, **kwargs):
    if not created:
        return

    from .models import Enrollment

    program = instance.program
    # Find all active students enrolled in this program
    enrollments = Enrollment.objects.filter(
        program=program, active=True, student__graduated=False
    ).select_related("student")

    for enrollment in enrollments:
        student = enrollment.student
        # If the fee is assigned to specific students, only notify those
        if (
            instance.assignments.exists()
            and not instance.assignments.filter(student=student).exists()
        ):
            continue

        _send_fee_notification(student, program, instance)


@receiver(post_save, sender="programs.Enrollment")
def notify_parents_on_enrollment(sender, instance, created, **kwargs):
    if not created:
        return

    from .models import Fee

    student = instance.student
    program = instance.program

    # Find all fees for this program
    fees = Fee.objects.filter(program=program)

    for fee in fees:
        # If the fee is assigned to specific students, only notify if this student is one of them
        if (
            fee.assignments.exists()
            and not fee.assignments.filter(student=student).exists()
        ):
            continue

        _send_fee_notification(student, program, fee)


@receiver(post_save, sender="programs.FeeAssignment")
def notify_parents_on_fee_assignment(sender, instance, created, **kwargs):
    if not created:
        return

    _send_fee_notification(instance.student, instance.fee.program, instance.fee)


@receiver(post_save, sender="programs.Enrollment")
def flag_clearance_due_on_enrollment(sender, instance, created, **kwargs):
    """Auto-flag an enrollment when the enrolled student requires PA clearances
    but is missing at least one valid clearance.

    The requirement is always derived from the student's date of birth; only
    the clearance records themselves are stored state.
    """
    from .models import BackgroundCheckType

    student = instance.student
    if not student or not student.requires_background_check():
        if instance.clearance_due:
            sender.objects.filter(pk=instance.pk).update(clearance_due=False)
        return

    required_types = set(BackgroundCheckType.values)
    valid_types = {
        bc.check_type for bc in student.background_checks.all() if bc.is_valid
    }
    needs = not required_types.issubset(valid_types)
    if needs != instance.clearance_due:
        sender.objects.filter(pk=instance.pk).update(clearance_due=needs)


def _send_fee_notification(student, program, fee):
    from .models import Enrollment
    from .utils import send_templated_notification

    # Never notify for inactive students (graduated or deactivated enrollment)
    if student.graduated:
        return
    if (
        Enrollment.objects.filter(student=student, program=program)
        .exclude(active=True)
        .exists()
    ):
        return

    parents = [
        p
        for p in student.all_parents
        if p.login_enabled and p.email_updates and p.personal_email
    ]
    if not parents:
        return

    subject = f"New Fee Added: {fee.name} for {program.name}"
    context = {
        "program": program,
        "student": student,
        "fee": fee,
    }
    recipient_list = [p.personal_email for p in parents]
    send_templated_notification(
        subject, "programs/emails/fee_added.html", context, recipient_list
    )


@receiver(post_save, sender="programs.Payment")
def notify_parents_on_payment_added(sender, instance, created, **kwargs):
    if not created:
        return

    from .utils import get_student_balance_data, send_templated_notification

    student = instance.student
    program = instance.program
    parents = [
        p
        for p in student.all_parents
        if p.login_enabled and p.email_updates and p.personal_email
    ]

    if not parents:
        return

    balance_data = get_student_balance_data(student, program)
    balance = balance_data["balance"]

    via = dict(instance.PAID_VIA_CHOICES).get(instance.paid_via, instance.paid_via)
    details = (
        f" (check #{instance.check_number})"
        if (instance.paid_via == "check" and instance.check_number)
        else ""
    )
    if instance.paid_via == "other" and instance.notes:
        details += f" — {instance.notes}"

    subject = f"Payment Recorded for {student} - {program.name}"
    context = {
        "student": student,
        "program": program,
        "payment": instance,
        "via": via,
        "details": details,
        "balance": balance,
        "entries": balance_data["entries"],
    }
    recipient_list = [p.personal_email for p in parents]
    send_templated_notification(
        subject, "programs/emails/payment_recorded.html", context, recipient_list
    )


@receiver(pre_save, sender="programs.SlidingScale")
def _capture_old_sliding_scale_status(sender, instance, **kwargs):
    """Stash the previous status on the instance so post_save can detect transitions."""
    if instance.pk:
        try:
            instance._old_status = (
                sender.objects.only("status").get(pk=instance.pk).status
            )
        except sender.DoesNotExist:
            instance._old_status = None
    else:
        instance._old_status = None


@receiver(post_save, sender="programs.SlidingScale")
def notify_parents_on_sliding_scale_added(sender, instance, created, **kwargs):
    from .models import SlidingScale

    student = instance.student
    old_status = getattr(instance, "_old_status", None)

    if created and instance.status == SlidingScale.STATUS_PENDING:
        _notify_sliding_scale_submitted(student, instance)
        return

    if created and instance.status in (
        SlidingScale.STATUS_APPROVED,
        SlidingScale.STATUS_DECLINED,
    ):
        # Manually created directly as approved/declined (e.g. by a Lead Mentor).
        _notify_sliding_scale_processed(student, instance)
        return

    if (
        not created
        and old_status == SlidingScale.STATUS_PENDING
        and instance.status
        in (SlidingScale.STATUS_APPROVED, SlidingScale.STATUS_DECLINED)
    ):
        # Auto-delete uploaded tax/personal documents once the application has
        # been reviewed — we don't want to hold on to them.
        for tax_form in instance.tax_forms.all():
            try:
                tax_form.file.delete(save=False)
            except OSError:
                # The underlying file may still be locked by another
                # process (e.g. on Windows, if it was just previewed or
                # downloaded). Don't let that block the review decision —
                # the DB record is removed below regardless.
                logger.warning(
                    "Could not delete tax form file %s for SlidingScale %s; "
                    "it may still be in use by another process.",
                    tax_form.file.name,
                    instance.pk,
                )
            tax_form.delete()
        _notify_sliding_scale_processed(student, instance)


def _notify_sliding_scale_submitted(student, sliding_scale):
    from .utils import get_lead_mentor_notification_email, send_templated_notification

    context = {"student": student, "sliding_scale": sliding_scale}

    parents = [
        p
        for p in student.all_parents
        if p.login_enabled and p.email_updates and p.personal_email
    ]
    if parents:
        send_templated_notification(
            f"Sliding Scale Application Submitted for {student}",
            "programs/emails/sliding_scale_submitted.html",
            context,
            [p.personal_email for p in parents],
        )

    send_templated_notification(
        f"New Sliding Scale Application to Review: {student}",
        "programs/emails/sliding_scale_submitted_lead_mentor.html",
        context,
        [get_lead_mentor_notification_email()],
    )


def _notify_sliding_scale_processed(student, sliding_scale):
    from .utils import send_templated_notification

    parents = [
        p
        for p in student.all_parents
        if p.login_enabled and p.email_updates and p.personal_email
    ]
    if not parents:
        return

    verb = "Approved" if sliding_scale.status == "approved" else "Update"
    subject = f"Sliding Scale Application {verb} for {student}"
    context = {"student": student, "sliding_scale": sliding_scale}
    send_templated_notification(
        subject,
        "programs/emails/sliding_scale_processed.html",
        context,
        [p.personal_email for p in parents],
    )
