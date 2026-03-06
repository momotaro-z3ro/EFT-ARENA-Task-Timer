import sqlite3
import datetime

conn = sqlite3.connect('eft_bot.db')
cursor = conn.cursor()

# Set to 4 hours in the past
past_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=4)
past_str = past_time.isoformat()

cursor.execute('''
    UPDATE users 
    SET eft_daily_deadline = ?,
        eft_weekly_deadline = ?,
        arena_daily_deadline = ?,
        arena_weekly_deadline = ?,
        eft_daily_completed = 1,
        eft_weekly_completed = 1,
        arena_daily_completed = 1,
        arena_weekly_completed = 1
''', (past_str, past_str, past_str, past_str))

conn.commit()
conn.close()
print("Time travel successful! All deadlines set to 4 hours ago and marked completed.")
