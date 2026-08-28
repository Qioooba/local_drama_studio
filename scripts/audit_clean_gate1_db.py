import json

from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database

db = Database(Settings().database_path)
conn = db.connect()

shots = conn.execute("SELECT * FROM shots WHERE episode_id='a15be941-b6d2-47c3-be97-b0548220262f' ORDER BY order_key ASC").fetchall()
print(f"=== DATABASE SHOTS (Total: {len(shots)}) ===")
total_duration_s = 0.0
for s in shots:
    s_dict = dict(s)
    rev = conn.execute("SELECT * FROM shot_revisions WHERE id=?", (s_dict['current_revision_id'],)).fetchone()
    fields = json.loads(rev['fields_json']) if rev else {}
    dur = float(fields.get('duration_seconds', 0))
    total_duration_s += dur
    print(f"Shot ID: {s_dict['id']}, code: {s_dict['code']}, order_key: {s_dict['order_key']}, status: {s_dict['status']}")
    print(f"  Rev fields: duration={dur}s, visual={fields.get('visual')}, action={fields.get('action')}")

print(f"\nTotal Episode Planned Duration: {total_duration_s}s")

scenes = conn.execute("SELECT * FROM scenes WHERE project_id='97c309b0-bf45-4dca-bd0c-006b76080be8' ORDER BY created_at ASC").fetchall()
print(f"\n=== DATABASE SCENES (Total: {len(scenes)}) ===")
for sc in scenes:
    sc_dict = dict(sc)
    print(f"Scene ID: {sc_dict['id']}, code={sc_dict['code']}, title={sc_dict['title']}")
