import os

import django

os.environ["DJANGO_SETTINGS_MODULE"] = "GoSAdminPortal.settings"
django.setup()
from django.db import connection

c = connection.cursor()
c.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='programs_mentorandrewaccess'"
)
print("orphan table exists:", bool(c.fetchone()))
c.execute("DROP TABLE IF EXISTS programs_mentorandrewaccess")
c.execute(
    "DELETE FROM django_migrations WHERE app='programs' AND name IN ('0078_create_mentor_andrew_access','0079_consolidate_adult_emails_and_mentor_andrew_access','0078_consolidate_adult_emails_and_mentor_andrew_access')"
)
print("stale migration rows deleted:", c.rowcount)
connection.commit()
print("done")
