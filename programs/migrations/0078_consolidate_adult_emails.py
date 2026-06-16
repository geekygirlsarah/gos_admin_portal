# Hand-written migration to consolidate Adult email fields and update Andrew ID
# field help_text/attributes. The DB was partially migrated by earlier aborted
# runs, so this migration uses SeparateDatabaseAndState throughout to reconcile
# the Django migration state with the actual DB schema without touching columns
# that were already dropped or were never added.

import django.db.models.deletion
from django.db import migrations, models


def add_andrew_columns_if_missing(apps, schema_editor):
    """Add andrew_id / andrew_email / andrew_id_expiration / andrew_id_sponsor_id
    to programs_adult if they don't already exist (idempotent)."""
    db = schema_editor.connection
    cols = [
        r[1]
        for r in db.cursor().execute("PRAGMA table_info(programs_adult)").fetchall()
    ]
    cur = db.cursor()
    if "andrew_id" not in cols:
        cur.execute("ALTER TABLE programs_adult ADD COLUMN andrew_id varchar(50) NULL")
    if "andrew_email" not in cols:
        cur.execute(
            "ALTER TABLE programs_adult ADD COLUMN andrew_email varchar(254) NULL"
        )
    if "andrew_id_expiration" not in cols:
        cur.execute(
            "ALTER TABLE programs_adult ADD COLUMN andrew_id_expiration date NULL"
        )
    if "andrew_id_sponsor_id" not in cols:
        cur.execute(
            "ALTER TABLE programs_adult ADD COLUMN andrew_id_sponsor_id bigint NULL REFERENCES programs_adult(id) DEFERRABLE INITIALLY DEFERRED"
        )


def remove_andrew_columns(apps, schema_editor):
    """Reverse: SQLite doesn't support DROP COLUMN easily; leave them in place."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0077_remove_student_student_active_graduated_idx_and_more"),
    ]

    operations = [
        # 1. Reconcile removed fields (already gone from DB; just update state)
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(model_name="adult", name="alumni_email"),
                migrations.RemoveField(model_name="adult", name="email"),
            ],
        ),
        # 2. Add Andrew fields to DB (idempotent) and update state with help_text
        migrations.RunPython(add_andrew_columns_if_missing, remove_andrew_columns),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AddField(
                    model_name="adult",
                    name="andrew_id",
                    field=models.CharField(
                        blank=True,
                        null=True,
                        max_length=50,
                        help_text="CMU Andrew ID. Assigned by lead mentors; only applies to mentors/CMU staff.",
                    ),
                ),
                migrations.AddField(
                    model_name="adult",
                    name="andrew_email",
                    field=models.EmailField(
                        blank=True,
                        null=True,
                        help_text="CMU Andrew email (andrew_id@andrew.cmu.edu). Assigned by lead mentors.",
                    ),
                ),
                migrations.AddField(
                    model_name="adult",
                    name="andrew_id_expiration",
                    field=models.DateField(
                        blank=True,
                        null=True,
                        help_text="Expiration date of this Andrew ID.",
                    ),
                ),
                migrations.AddField(
                    model_name="adult",
                    name="andrew_id_sponsor",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="sponsored_andrew_ids",
                        to="programs.adult",
                        help_text="The Adult (mentor) who sponsored this Andrew ID.",
                    ),
                ),
            ],
        ),
        # 3. Update personal_email to be unique with help_text (already unique in DB from 0077)
        migrations.AlterField(
            model_name="adult",
            name="personal_email",
            field=models.EmailField(
                blank=True,
                null=True,
                unique=True,
                help_text="Primary contact email (e.g. Gmail). Used for login and notifications.",
            ),
        ),
    ]
