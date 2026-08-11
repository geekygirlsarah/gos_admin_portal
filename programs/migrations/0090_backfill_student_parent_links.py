from django.db import migrations


def backfill_parent_links(apps, schema_editor):
    """Backfill AdultStudentRelationship rows from Student FKs.

    A Student could have primary_contact / secondary_contact set without
    the matching AdultStudentRelationship M2M row (e.g. data orphaned by
    the 0075 through-model migration). That made the parent side
    (Adult.all_students, which reads only the M2M) miss the student.
    Idempotent: existing M2M rows are never duplicated or overwritten.
    """
    Student = apps.get_model("programs", "Student")
    AdultStudentRelationship = apps.get_model("programs", "AdultStudentRelationship")

    for student in Student.objects.exclude(primary_contact__isnull=True).iterator():
        AdultStudentRelationship.objects.get_or_create(
            adult_id=student.primary_contact_id,
            student_id=student.pk,
            defaults={"relationship_to_student": "parent"},
        )
    for student in Student.objects.exclude(secondary_contact__isnull=True).iterator():
        AdultStudentRelationship.objects.get_or_create(
            adult_id=student.secondary_contact_id,
            student_id=student.pk,
            defaults={"relationship_to_student": "parent"},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0089_rolepermission_mentor_attendance_write"),
    ]

    operations = [
        migrations.RunPython(backfill_parent_links, migrations.RunPython.noop),
    ]
