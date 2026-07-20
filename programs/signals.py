import logging

from django.apps import apps
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_migrate, post_save
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

    # Lead mentors: all perms
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


@receiver(post_save, sender=lambda: apps.get_model("programs", "Adult"))
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


@receiver(post_save, sender=lambda: apps.get_model("programs", "Student"))
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

    from django.conf import settings
    from django.core.mail import send_mail

    from .models import Enrollment

    program = instance.program
    # Find all students enrolled in this program
    enrollments = Enrollment.objects.filter(program=program).select_related("student")

    for enrollment in enrollments:
        student = enrollment.student
        # If the fee is assigned to specific students, only notify those
        if (
            instance.assignments.exists()
            and not instance.assignments.filter(student=student).exists()
        ):
            continue

        parents = [
            p for p in student.all_parents if p.email_updates and p.personal_email
        ]
        if not parents:
            continue

        subject = f"New Fee Added: {instance.name} for {program.name}"
        message = (
            f"A new fee has been added to the program {program.name} for {student}:\n\n"
            f"Fee Name: {instance.name}\n"
            f"Amount: ${instance.amount}\n"
            f"Date: {instance.date or instance.created_at.date()}\n\n"
            "Log in to the portal to view the full balance sheet."
        )
        from_email = settings.DEFAULT_FROM_EMAIL
        recipient_list = [p.personal_email for p in parents]

        # Send individual emails to avoid leaking other parents' emails if they were grouped
        for parent_email in recipient_list:
            send_mail(subject, message, from_email, [parent_email])


@receiver(post_save, sender="programs.Payment")
def notify_parents_on_payment_added(sender, instance, created, **kwargs):
    if not created:
        return

    from django.conf import settings
    from django.core.mail import send_mail

    from .utils import get_student_balance_data

    student = instance.student
    program = instance.program
    parents = [p for p in student.all_parents if p.email_updates and p.personal_email]

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
    message = (
        f"A payment has been recorded for {student} in the program {program.name}:\n\n"
        f"Amount: ${instance.amount}\n"
        f"Paid on: {instance.paid_on}\n"
        f"Paid via: {via}{details}\n"
        f"Notes: {instance.notes or 'N/A'}\n\n"
        f"Your remaining balance for this program is: ${balance:,.2f}\n\n"
        "Thank you!"
    )
    from_email = settings.DEFAULT_FROM_EMAIL
    for p in parents:
        send_mail(subject, message, from_email, [p.personal_email])


@receiver(post_save, sender="programs.SlidingScale")
def notify_parents_on_sliding_scale_added(sender, instance, created, **kwargs):
    if not created:
        return

    from django.conf import settings
    from django.core.mail import send_mail

    student = instance.student
    program = instance.program
    parents = [p for p in student.all_parents if p.email_updates and p.personal_email]

    if not parents:
        return

    subject = f"Sliding Scale Added for {student} - {program.name}"
    message = (
        f"A sliding scale reduction has been added for {student} in the program {program.name}:\n\n"
        f"Reduction Percentage: {instance.percent}%\n"
        f"Effective Date: {instance.date or instance.created_at.date()}\n\n"
        "This discount will be applied to applicable fees in your balance sheet."
    )
    from_email = settings.DEFAULT_FROM_EMAIL
    for p in parents:
        send_mail(subject, message, from_email, [p.personal_email])
