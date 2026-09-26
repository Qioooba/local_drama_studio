import sqlite3

conn = sqlite3.connect("data/local_drama.sqlite3")
conn.row_factory = sqlite3.Row
c = conn.cursor()

c.execute("SELECT state, count(*) FROM jobs WHERE type = 'MODEL_PLATFORM_EXECUTION' GROUP BY state")
print("Model Execution Jobs:", dict(c.fetchall()))

c.execute("""
    SELECT b.code, b.id, count(c.id) as cand_count,
           sum(case when c.status='READY' then 1 else 0 end) as ready_count,
           sum(case when c.adopted=1 then 1 else 0 end) as adopted_count
    FROM explainer_visual_beats b
    LEFT JOIN explainer_media_candidates c ON b.id = c.beat_id AND c.purpose = 'KEYFRAME'
    WHERE b.video_id = '2339c1c3-79c0-48a2-880a-f7bc2956bcd2'
    GROUP BY b.id
    ORDER BY b.code
""")
rows = c.fetchall()
ready_beats = sum(1 for r in rows if r['ready_count'] > 0)
adopted_beats = sum(1 for r in rows if r['adopted_count'] > 0)
print(f"Keyframe Ready beats: {ready_beats} / {len(rows)}, Adopted beats: {adopted_beats} / {len(rows)}")
for r in rows:
    status_str = "READY" if r['ready_count'] > 0 else "PENDING"
    print(f"  {r['code']}: {status_str} (cands={r['cand_count']})")
