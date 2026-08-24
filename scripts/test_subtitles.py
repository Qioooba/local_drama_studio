import sys
from pathlib import Path

# Add project root to sys.path
root = Path(r"F:\AI_Projects\h3\local_drama_studio")
sys.path.insert(0, str(root / "apps" / "api"))

from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database
from local_drama.application.timeline import TimelineService

settings = Settings()
db = Database(settings.database_path)
timeline = TimelineService(db, settings)

PROJECT_ID = "9893a9bc-e58b-45a2-9143-c1bd7b886db9"
EPISODE_ID = "b989644a-666e-448c-968b-6b865dbebca7"

with db.connect() as conn:
    row = conn.execute(
        """SELECT sdv.id, sdv.extracted_text_rel, p.root_rel 
        FROM source_document_versions sdv 
        JOIN source_documents sd ON sd.id=sdv.source_document_id 
        JOIN projects p ON p.id=sd.project_id 
        WHERE sd.project_id=?""",
        (PROJECT_ID,)
    ).fetchone()

path = settings.projects_root / row["root_rel"] / row["extracted_text_rel"]
text = path.read_text("utf-8")
print("Full Extracted Source Text:\n" + "="*40)
print(text)
print("="*40)

lines = [line.strip() for line in text.split("\n") if line.strip()]
print(f"Found {len(lines)} non-empty lines:")
for i, l in enumerate(lines):
    print(f"Line {i+1}: {l[:40]}...")

# Build subtitle cues from the actual lines in exact order
cues = []
cur_t = 1_000_000
for i, l in enumerate(lines[:4]):
    cues.append({
        "start_us": cur_t,
        "end_us": cur_t + 5_000_000,
        "text": l,
    })
    cur_t += 6_000_000

sub = timeline.create_subtitle_revision(
    episode_id=EPISODE_ID,
    cues=cues,
    format="SRT",
    authority={
        "text_authority": "SCRIPT",
        "source_document_version_id": str(row["id"]),
    },
)
print("Successfully created subtitle revision:", sub["id"])
