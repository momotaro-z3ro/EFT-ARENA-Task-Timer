import database
import datetime
db = database.Database('eft_bot.db')
print("Direct fetch from DB users:")
db.cursor.execute("SELECT * FROM users")
rows = db.cursor.fetchall()
col_names = [description[0] for description in db.cursor.description]
for row in rows:
    d = dict(zip(col_names, row))
    print(f"ID: {d['user_id']}")
    for k, v in d.items():
        if v and str(v) != '0' and k != 'user_id':
             print(f"  {k}: {v}")
