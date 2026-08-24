import json
import urllib.request

base = 'http://127.0.0.1:3210/api/v1'
pid = '97c309b0-bf45-4dca-bd0c-006b76080be8'

def get_json(url):
    req = urllib.request.Request(url, headers={'Accept': 'application/json'})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode('utf-8'))

print("=== 1. PROJECT DETAILS ===")
proj = get_json(f'{base}/projects/{pid}')
print(json.dumps(proj, indent=2, ensure_ascii=False))

print("\n=== 2. SEASONS & EPISODES ===")
seasons = get_json(f'{base}/projects/{pid}/seasons')
sid = seasons['items'][0]['id']
episodes = get_json(f'{base}/projects/seasons/{sid}/episodes')
eid = episodes['items'][0]['id']
print(f"Season ID: {sid}, Episode ID: {eid}")

print("\n=== 3. JOBS FOR PROJECT ===")
jobs = get_json(f'{base}/jobs?project_id={pid}')
print(f"Total Jobs: {len(jobs.get('items', []))}")
for j in jobs.get('items', []):
    print(f"  Job {j.get('id')}: type={j.get('type')}, state={j.get('state')}, progress={j.get('progress')}")

print("\n=== 4. SHOTS LIST ===")
shots_data = get_json(f'{base}/projects/episodes/{eid}/shots')
shots = shots_data.get('items', [])
print(f"Total Shots: {len(shots)}")
total_dur = sum(float(s.get('duration_seconds') or 0) for s in shots)
print(f"Total Duration: {total_dur}s")
for s in shots:
    print(f"  Shot {s.get('shot_no')}: ID={s.get('id')}, dur={s.get('duration_seconds')}s, visual={str(s.get('visual'))[:30]}")

print("\n=== 5. SCENES ===")
scenes = get_json(f'{base}/projects/{pid}/scenes')
print(f"Total Scenes: {len(scenes.get('items', []))}")
for sc in scenes.get('items', []):
    print(f"  Scene: ID={sc.get('id')}, code={sc.get('code')}, title={sc.get('title')}, location={sc.get('location')}")

print("\n=== 6. DIALOGUE LINES ===")
dialogues = get_json(f'{base}/episodes/{eid}/dialogue-lines')
print(f"Total Dialogue Lines: {len(dialogues.get('items', []))}")
for d in dialogues.get('items', []):
    print(f"  Line {d.get('line_no')}: ID={d.get('id')}, speaker={d.get('speaker_name')}, text={d.get('text')}")
