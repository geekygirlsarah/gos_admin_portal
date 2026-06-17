import os

import django

os.environ["DJANGO_SETTINGS_MODULE"] = "GoSAdminPortal.settings"
django.setup()
from django.db import connection

with connection.cursor() as c:
    if connection.vendor == "sqlite":
        c.execute(
            "UPDATE programs_adult SET andrew_id_sponsor_id = NULL WHERE typeof(andrew_id_sponsor_id) = 'text'"
        )
        print("rows fixed:", c.rowcount)
    else:
        print("Skipping fix_fk.py: only applicable to SQLite")
connection.commit()
