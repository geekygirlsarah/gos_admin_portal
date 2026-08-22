from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("outreach", "0004_convert_dates_to_shifts"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="outreachevent",
            options={},
        ),
        migrations.RemoveField(
            model_name="outreachevent",
            name="end_date",
        ),
        migrations.RemoveField(
            model_name="outreachevent",
            name="end_time",
        ),
        migrations.RemoveField(
            model_name="outreachevent",
            name="start_date",
        ),
        migrations.RemoveField(
            model_name="outreachevent",
            name="start_time",
        ),
    ]
