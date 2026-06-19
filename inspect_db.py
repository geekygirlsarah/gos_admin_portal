import os

import django

os.environ["DJANGO_SETTINGS_MODULE"] = "GoSAdminPortal.settings"
django.setup()
from django.db import connection

with connection.cursor() as c:
    cols = [
        column.name
        for column in connection.introspection.get_table_description(
            c, "programs_adult"
        )
    ]
    print("adult columns:", cols)
    c.execute(
        "SELECT name FROM django_migrations WHERE app='programs' ORDER BY id DESC LIMIT 5"
    )
    print("last migrations:", [r[0] for r in c.fetchall()])
