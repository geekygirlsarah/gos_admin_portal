import os, django
os.environ["DJANGO_SETTINGS_MODULE"] = "GoSAdminPortal.settings"
django.setup()
from django.db import connection
c = connection.cursor()
cols = [r[1] for r in c.execute("PRAGMA table_info(programs_adult)").fetchall()]
print("adult columns:", cols)
c.execute("SELECT name FROM django_migrations WHERE app='programs' ORDER BY id DESC LIMIT 5")
print("last migrations:", [r[0] for r in c.fetchall()])
