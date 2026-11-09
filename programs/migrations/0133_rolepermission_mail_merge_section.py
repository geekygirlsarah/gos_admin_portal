from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0132_emaildeliveryevent_recipient_fingerprint_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="rolepermission",
            name="section",
            field=models.CharField(
                max_length=50,
                choices=[
                    ("student_info", "Student - Info (General)"),
                    ("identity", "Student - Identity"),
                    ("contact_address", "Student - Contact & Address"),
                    ("health_medical", "Student - Health & Medical"),
                    ("school", "Student - School"),
                    ("cmu_andrew", "Student - CMU Andrew ID"),
                    ("background_checks", "Student - Background Checks"),
                    ("student_documents", "Student - Signed Documents"),
                    ("discord", "Student - Discord"),
                    ("first_website", "Student - FIRST Website"),
                    ("parents_emergency", "Student - Parents/Emergency Contacts"),
                    ("other_details", "Student - Other Details"),
                    ("attendance", "Student - Attendance"),
                    ("adult_info", "Adult - Info"),
                    ("payments", "Payments - General"),
                    ("sliding_scale", "Payments - Sliding Scale"),
                    ("fees", "Programs - Fees"),
                    ("programs", "Programs - General"),
                    ("mail_merge", "Messaging - Mail Merge"),
                    ("team_assignments", "Programs - Team Assignments"),
                    ("badge_award", "Badges - Award"),
                    ("badge_manage", "Badges - Create / Manage"),
                    ("orders-view", "Orders - See requested orders"),
                    ("orders-request", "Orders - Place item request"),
                    ("orders-manage", "Orders - Place new orders"),
                    ("orders-shipping", "Orders - Add shipping"),
                    ("calendar", "Calendar"),
                ],
            ),
        ),
    ]
