import os, django
os.environ["DJANGO_SETTINGS_MODULE"] = "GoSAdminPortal.settings"
django.setup()
from django.db import connection
c = connection.cursor()
c.execute("UPDATE programs_adult SET andrew_id_sponsor_id = NULL WHERE typeof(andrew_id_sponsor_id) = 'text'")
print("rows fixed:", c.rowcount)
connection.commit()
