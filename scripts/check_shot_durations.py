from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database

db = Database(Settings().database_path)
conn = db.connect()

shots = conn.execute("SELECT id, code, order_key, target_duration_ms, status FROM shots WHERE episode_id='a15be941-b6d2-47c3-be97-b0548220262f' ORDER BY order_key ASC").fetchall()
total_ms = sum(s['target_duration_ms'] or 0 for s in shots)
print(f"Total target_duration_ms: {total_ms} ms ({total_ms/1000.0} s)")
for s in shots:
    print(f"Shot {s['code']} (ID: {s['id']}): target_duration = {s['target_duration_ms']/1000.0}s, status = {s['status']}")
