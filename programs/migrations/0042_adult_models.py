from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0041_add_signout_feature"),
    ]

    operations = [
        migrations.CreateModel(
            name="Adult",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("first_name", models.CharField(max_length=150)),
                ("preferred_first_name", models.CharField(blank=True, max_length=150, null=True)),
                ("last_name", models.CharField(max_length=150)),
                ("pronouns", models.CharField(blank=True, max_length=50, null=True)),
                ("email", models.EmailField(blank=True, max_length=254, null=True)),
                ("phone_number", models.CharField(blank=True, max_length=30, null=True)),
                ("address_line1", models.CharField(blank=True, max_length=200, null=True)),
                ("address_line2", models.CharField(blank=True, max_length=200, null=True)),
                ("city", models.CharField(blank=True, max_length=100, null=True)),
                ("state", models.CharField(blank=True, max_length=100, null=True)),
                ("postal_code", models.CharField(blank=True, max_length=20, null=True)),
                ("discord_username", models.CharField(blank=True, max_length=100, null=True)),
                ("photo", models.ImageField(blank=True, null=True, upload_to="photos/adults/")),
                ("notes", models.TextField(blank=True)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["last_name", "first_name"],
            },
        ),
        migrations.CreateModel(
            name="StudentRelationship",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("type", models.CharField(choices=[("parent", "Parent"), ("guardian", "Guardian"), ("emergency_contact", "Emergency contact"), ("other", "Other")], max_length=32)),
                ("is_primary", models.BooleanField(default=False)),
                ("start_date", models.DateField(blank=True, null=True)),
                ("end_date", models.DateField(blank=True, null=True)),
                ("notes", models.TextField(blank=True)),
                ("adult", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="student_links", to="programs.adult")),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="adult_links", to="programs.student")),
            ],
            options={
                "ordering": ["student__last_name", "adult__last_name"],
                "verbose_name": "Student Relationship",
                "verbose_name_plural": "Student Relationships",
                "unique_together": {("adult", "student", "type")},
            },
        ),
        migrations.CreateModel(
            name="AdultProgramRole",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("role", models.CharField(choices=[("mentor", "Mentor"), ("volunteer", "Volunteer"), ("chaperone", "Chaperone")], max_length=32)),
                ("active", models.BooleanField(default=True)),
                ("background_check_date", models.DateField(blank=True, null=True)),
                ("clearance_expires", models.DateField(blank=True, null=True)),
                ("training_completed", models.BooleanField(default=False)),
                ("access_badge_id", models.CharField(blank=True, max_length=50)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("adult", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="program_roles", to="programs.adult")),
                ("program", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="adult_roles", to="programs.program")),
            ],
            options={
                "ordering": ["program__name", "adult__last_name"],
                "verbose_name": "Adult Program Role",
                "verbose_name_plural": "Adult Program Roles",
                "unique_together": {("adult", "program", "role")},
            },
        ),
    ]
