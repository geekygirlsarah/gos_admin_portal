from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0062_alter_adult_active_alter_adult_is_alumni_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="adult",
            name="specific_relationship",
            field=models.CharField(
                blank=True,
                help_text="Specific relationship, e.g. father, stepmom, foster parent, etc.",
                max_length=100,
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="adult",
            name="relationship_to_student",
            field=models.CharField(
                choices=[
                    ("parent", "Parent"),
                    ("grandparent", "Grandparent"),
                    ("pibling", "Pibling (aunt/uncle)"),
                    ("sibling", "Sibling"),
                    ("guardian", "Guardian"),
                    ("family_friend", "Family Friend"),
                    ("other", "Other"),
                ],
                default="parent",
                max_length=20,
            ),
        ),
    ]
