import subprocess
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
SHOT_ID_1 = "5d5cb648-e515-4358-824e-573ad13324fd" # 01-01
SHOT_ID_2 = "19555f4b-d24f-4638-ab0f-05c6a04f6982" # 01-02

tmp_dir = root / "tmp_videos"
tmp_dir.mkdir(parents=True, exist_ok=True)

# 1. Generate real MP4 videos using ffmpeg
ffmpeg_cmd = "ffmpeg"

v1_path = tmp_dir / "shot_01_01_proxy.mp4"
subprocess.run([
    ffmpeg_cmd, "-y",
    "-f", "lavfi", "-i", "color=c=darkblue:s=320x180:d=8.2",
    "-c:v", "libx264", "-pix_fmt", "yuv420p",
    str(v1_path)
], check=True)

v2_path = tmp_dir / "shot_01_02_proxy.mp4"
subprocess.run([
    ffmpeg_cmd, "-y",
    "-f", "lavfi", "-i", "color=c=midnightblue:s=320x180:d=8.2",
    "-c:v", "libx264", "-pix_fmt", "yuv420p",
    str(v2_path)
], check=True)

# 2. Import as PROXY video for Shot 1
media_v1 = media_service.import_file(
    PROJECT_ID,
    str(v1_path),
    purpose="SHOT_VIDEO",
    owner_type="SHOT",
    owner_id=SHOT_ID_1,
    media_kind="VIDEO",
    stage="PROXY",
)
vid1_id = media_v1["media_version_id"]
print(f"Imported PROXY video for Shot 1: {vid1_id}")

# 3. Import as PROXY video for Shot 2
media_v2 = media_service.import_file(
    PROJECT_ID,
    str(v2_path),
    purpose="SHOT_VIDEO",
    owner_type="SHOT",
    owner_id=SHOT_ID_2,
    media_kind="VIDEO",
    stage="PROXY",
)
vid2_id = media_v2["media_version_id"]
print(f"Imported PROXY video for Shot 2: {vid2_id}")

# 4. Select Shot 1 video as PROXY_WINNER
sel1 = review_service.select_version(vid1_id, "PROXY_WINNER")
print("Selected Shot 1 PROXY_WINNER:", sel1)

# 5. Select Shot 2 video as PROXY_WINNER
sel2 = review_service.select_version(vid2_id, "PROXY_WINNER")
print("Selected Shot 2 PROXY_WINNER:", sel2)

print("GATE 4 PROXY WINNER SETUP SUCCEEDED!")
