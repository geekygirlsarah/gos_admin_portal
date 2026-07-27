from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0004_add_visitor_team_number"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="kioskconfig",
            name="api_key",
        ),
    ]
