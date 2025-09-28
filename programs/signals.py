from django.apps import apps
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_migrate, post_save
from django.dispatch import receiver

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
    Program = apps.get_model('programs', 'Program')
    program_ct = ContentType.objects.get_for_model(Program)

    # Student model may not exist on very first migrate run
    try:
        Student = apps.get_model('programs', 'Student')
        student_ct = ContentType.objects.get_for_model(Student)
    except Exception:
        Student = None
        student_ct = None

    program_perms = {
        'add_program': Permission.objects.get(codename='add_program', content_type=program_ct),
        'change_program': Permission.objects.get(codename='change_program', content_type=program_ct),
        'delete_program': Permission.objects.get(codename='delete_program', content_type=program_ct),
        'view_program': Permission.objects.get(codename='view_program', content_type=program_ct),
    }

    student_perms = {}
    if student_ct:
        student_perms = {
            'add_student': Permission.objects.get(codename='add_student', content_type=student_ct),
            'change_student': Permission.objects.get(codename='change_student', content_type=student_ct),
            'delete_student': Permission.objects.get(codename='delete_student', content_type=student_ct),
            'view_student': Permission.objects.get(codename='view_student', content_type=student_ct),
        }

    lead = ensure_group('LeadMentor')
    mentor = ensure_group('Mentor')
    parent = ensure_group('Parent')
    student_group = ensure_group('Student')

    # Lead mentors: all perms
    lead.permissions.add(*program_perms.values())
    if student_perms:
        lead.permissions.add(*student_perms.values())

    # Mentors/Parents: can manage students, view programs
    mentor.permissions.add(program_perms['view_program'])
    parent.permissions.add(program_perms['view_program'])
    if student_perms:
        mentor.permissions.add(student_perms['view_student'], student_perms['change_student'], student_perms['add_student'])
        parent.permissions.add(student_perms['view_student'], student_perms['change_student'], student_perms['add_student'])

    # Students: view programs, view student (object-level restrictions handled in views if needed)
    student_group.permissions.add(program_perms['view_program'])
    if student_perms:
        student_group.permissions.add(student_perms['view_student'])


@receiver(post_migrate)
def create_roles_and_permissions(sender, app_config=None, **kwargs):
    try:
        for name in ROLE_GROUPS:
            ensure_group(name)
        assign_default_permissions()
    except Exception:
        # Avoid breaking migrate due to permissions wiring
        pass


@receiver(post_save, sender=lambda: apps.get_model('programs', 'Adult'))
def ensure_user_in_mentor_group(sender, instance, created, **kwargs):
    try:
        # Only add to Mentor group if this Adult is marked as mentor
        if instance.is_mentor and instance.user_id:
            group = ensure_group('Mentor')
            instance.user.groups.add(group)
    except Exception:
        # Avoid raising in signal handlers
        pass
