from django.db import migrations


def map_relationship(rel: str) -> str:
    # Normalize Parent.relationship_to_student to StudentRelationship types
    if not rel:
        return 'other'
    rel = rel.lower()
    if rel == 'guardian':
        return 'guardian'
    if rel in {
        'parent', 'mother', 'father', 'grandparent', 'grandmother', 'grandfather',
        'pibling', 'aunt', 'uncle', 'sibling', 'sister', 'brother'
    }:
        return 'parent'
    if rel in {'emergency', 'emergency_contact'}:
        return 'emergency_contact'
    return 'other'


def build_dedupe_key(first_name, last_name, email, phone):
    email = (email or '').strip().lower()
    if email:
        return f"email:{email}"
    # Fall back to name + phone
    key = f"name_phone:{(first_name or '').strip().lower()}|{(last_name or '').strip().lower()}|{(phone or '').strip()}"
    return key


def backfill_forward(apps, schema_editor):
    Parent = apps.get_model('programs', 'Parent')
    Mentor = apps.get_model('programs', 'Mentor')
    Adult = apps.get_model('programs', 'Adult')
    StudentRelationship = apps.get_model('programs', 'StudentRelationship')

    # In-memory map to avoid duplicates during one migration run
    dedupe = {}

    def get_or_create_adult_from_identity(first_name, preferred_first_name, last_name, pronouns,
                                          email, phone_number, discord_username=None, photo=None, active=True):
        key = build_dedupe_key(first_name, last_name, email, phone_number)
        adult = dedupe.get(key)
        if adult:
            return adult
        # Try to find an existing Adult created earlier in the run or from previous partial runs
        qs = Adult.objects.all()
        if email:
            qs = qs.filter(email=email)
        else:
            qs = qs.filter(first_name=first_name or '', last_name=last_name or '', phone_number=phone_number)
        adult = qs.first()
        if not adult:
            adult = Adult.objects.create(
                first_name=first_name or '',
                preferred_first_name=preferred_first_name,
                last_name=last_name or '',
                pronouns=pronouns,
                email=email,
                phone_number=phone_number,
                discord_username=discord_username,
                photo=photo,
                active=active,
            )
        else:
            # Opportunistically fill missing contact fields
            changed = False
            if not adult.email and email:
                adult.email = email
                changed = True
            if not adult.phone_number and phone_number:
                adult.phone_number = phone_number
                changed = True
            if not adult.discord_username and discord_username:
                adult.discord_username = discord_username
                changed = True
            if changed:
                adult.save(update_fields=['email', 'phone_number', 'discord_username', 'updated_at'])
        dedupe[key] = adult
        return adult

    # 1) Backfill Adults and StudentRelationships from Parents
    for parent in Parent.objects.all().iterator():
        adult = get_or_create_adult_from_identity(
            first_name=parent.first_name,
            preferred_first_name=parent.preferred_first_name,
            last_name=parent.last_name,
            pronouns=None,
            email=parent.email,
            phone_number=parent.phone_number,
            discord_username=None,
            photo=None,
            active=True,
        )
        rel_type = map_relationship(parent.relationship_to_student)
        # Link to all associated students
        for student in parent.students.all().iterator():
            StudentRelationship.objects.get_or_create(
                adult=adult,
                student=student,
                type=rel_type,
                defaults={
                    'is_primary': False,
                }
            )

    # 2) Backfill Adults from Mentors (no program-scoped roles yet)
    for mentor in Mentor.objects.all().iterator():
        get_or_create_adult_from_identity(
            first_name=mentor.first_name,
            preferred_first_name=mentor.preferred_first_name,
            last_name=mentor.last_name,
            pronouns=mentor.pronouns,
            email=mentor.personal_email,
            phone_number=mentor.cell_phone or mentor.home_phone,
            discord_username=mentor.discord_username,
            photo=mentor.photo,
            active=mentor.active,
        )


def backfill_reverse(apps, schema_editor):
    # Intentionally a no-op to avoid accidental data loss if rolled back.
    # The created Adult and StudentRelationship rows are safe to keep.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('programs', '0042_adult_models'),
    ]

    operations = [
        migrations.RunPython(backfill_forward, backfill_reverse),
    ]
