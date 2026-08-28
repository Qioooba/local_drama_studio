import sys
from pathlib import Path

# Add project root to sys.path
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "apps" / "api"))

from local_drama.application.media import MediaService
from local_drama.application.reviews import ReviewService
from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database

settings = Settings()
settings.ensure_roots()
db = Database(settings.database_path)
media_service = MediaService(db, settings)
review_service = ReviewService(db, settings)

PROJECT_ID = "9893a9bc-e58b-45a2-9143-c1bd7b886db9"
EPISODE_ID = "b989644a-666e-448c-968b-6b865dbebca7"
SHOT_ID = "5d5cb648-e515-4358-824e-573ad13324fd"
KEYFRAME_VERSION_ID = "6c7a17a8-c0d0-4a08-b0d2-fe9eb980f63a"

# 1. Create keyframe candidate for shot 01-01
candidate = media_service.create_keyframe_candidate(KEYFRAME_VERSION_ID, SHOT_ID, "HEAD")
cand_id = candidate["id"]
print(f"Created keyframe candidate {cand_id} (stage={candidate.get('stage')})")

# 2. Select this candidate as KEYFRAME
selection = review_service.select_version(cand_id, "KEYFRAME")
print("Selected candidate as KEYFRAME:", selection)

# 3. Create review inbox record / human review approval
print("Selection and candidate setup succeeded!")
