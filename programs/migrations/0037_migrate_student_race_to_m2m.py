from django.db import migrations


def forwards(apps, schema_editor):
    Student = apps.get_model('programs', 'Student')
    RaceEthnicity = apps.get_model('programs', 'RaceEthnicity')

    def match(text: str):
        if not text:
            return []
        s = (text or '').lower()
        keys = set()
        # American Indian or Alaska Native
        if 'american indian' in s or 'alaska' in s or 'native american' in s:
            keys.add('american-indian-or-alaska-native')
        # Asian
        if 'asian' in s:
            keys.add('asian')
        # Black or African-American
        if 'black' in s or 'african-american' in s or 'african american' in s:
            keys.add('black-or-african-american')
        # Hispanic or Latino
        if 'hispanic' in s or 'latino' in s or 'latina' in s or 'latinx' in s:
            keys.add('hispanic-or-latino')
        # Middle Eastern or North African
        if 'middle eastern' in s or 'north african' in s or 'mena' in s:
            keys.add('middle-eastern-or-north-african')
        # Native Hawaiian or Other Pacific Islander
        if 'hawaiian' in s or 'pacific islander' in s:
            keys.add('native-hawaiian-or-other-pacific-islander')
        # White
        if 'white' in s:
            keys.add('white')
        # Other
        if 'other' in s or (not keys and s.strip()):
            keys.add('other')
        return list(RaceEthnicity.objects.filter(key__in=keys).values_list('id', flat=True))

    for student in Student.objects.exclude(race_ethnicity__isnull=True).exclude(race_ethnicity__exact=''):
        ids = match(student.race_ethnicity)
        if ids:
            student.race_ethnicities.set(ids)


def backwards(apps, schema_editor):
    # Best-effort: join selections into a comma-separated string
    Student = apps.get_model('programs', 'Student')
    for student in Student.objects.all():
        names = list(student.race_ethnicities.values_list('name', flat=True))
        if names:
            student.race_ethnicity = ', '.join(names)
            student.save(update_fields=['race_ethnicity'])


class Migration(migrations.Migration):
    dependencies = [
        ('programs', '0036_race_ethnicity_m2m'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]