import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0125_rolepermission_organization"),
    ]

    operations = [
        # Adult field renames
        migrations.RenameField(
            model_name="adult",
            old_name="andrew_id",
            new_name="org_account_id",
        ),
        migrations.RenameField(
            model_name="adult",
            old_name="andrew_email",
            new_name="org_email",
        ),
        migrations.RenameField(
            model_name="adult",
            old_name="andrew_id_expiration",
            new_name="org_email_expiration",
        ),
        migrations.RenameField(
            model_name="adult",
            old_name="andrew_id_sponsor",
            new_name="org_email_sponsor",
        ),
        migrations.AlterField(
            model_name="adult",
            name="org_account_id",
            field=models.CharField(
                blank=True,
                help_text="Organization / institutional account ID (e.g. username or student ID).",
                max_length=50,
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="adult",
            name="org_email",
            field=models.EmailField(
                blank=True,
                help_text="Organization / institutional email address.",
                max_length=254,
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="adult",
            name="org_email_expiration",
            field=models.DateField(
                blank=True,
                help_text="Expiration date of this organization account / email.",
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="adult",
            name="org_email_sponsor",
            field=models.ForeignKey(
                blank=True,
                help_text="The Adult (mentor) who sponsored this organization account.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="sponsored_org_emails",
                to="programs.adult",
            ),
        ),
        # Student field renames
        migrations.RenameField(
            model_name="student",
            old_name="andrew_id",
            new_name="org_account_id",
        ),
        migrations.RenameField(
            model_name="student",
            old_name="andrew_email",
            new_name="org_email",
        ),
        migrations.RenameField(
            model_name="student",
            old_name="andrew_id_expiration",
            new_name="org_email_expiration",
        ),
        migrations.RenameField(
            model_name="student",
            old_name="andrew_id_sponsor",
            new_name="org_email_sponsor",
        ),
        migrations.AlterField(
            model_name="student",
            name="org_email_expiration",
            field=models.DateField(
                blank=True,
                help_text="Expiration date of this organization account / email.",
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="student",
            name="org_email_sponsor",
            field=models.ForeignKey(
                blank=True,
                help_text="The Adult (mentor) who sponsored this organization account.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="sponsored_student_org_emails",
                to="programs.adult",
            ),
        ),
    ]
