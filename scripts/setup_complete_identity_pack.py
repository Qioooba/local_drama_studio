import struct
import sys
import zlib
from pathlib import Path

# Add project root to sys.path
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "apps" / "api"))

from local_drama.application.character_identity_packs import (
    CharacterIdentityPackService,
)
from local_drama.application.media import MediaService
from local_drama.application.workspace_assets import WorkspaceAssetService
from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database


def make_solid_png(width, height, r, g, b):
    def chunk(tag, data):
        return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", zlib.crc32(tag + data) & 0xffffffff)

    raw_data = bytearray()
    for _ in range(height):
        raw_data.append(0) # filter type none
        for _ in range(width):
            raw_data.extend([r, g, b])
    
    compressed = zlib.compress(bytes(raw_data))
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", compressed)
    png += chunk(b"IEND", b"")
    return png

settings = Settings()
settings.ensure_roots()
db = Database(settings.database_path)
media_service = MediaService(db, settings)
workspace_service = WorkspaceAssetService(db, settings)
pack_service = CharacterIdentityPackService(db)

PROJECT_ID = "9893a9bc-e58b-45a2-9143-c1bd7b886db9"
EPISODE_ID = "b989644a-666e-448c-968b-6b865dbebca7"
LINMO_ID = "43def1f6-34ee-46a0-96e4-73ef82f9a0c5"
SHOT_ID = "5d5cb648-e515-4358-824e-573ad13324fd"

# 1. Create 3 distinct images for Front, Left, Right views
tmp_dir = root / "tmp_refs"
tmp_dir.mkdir(parents=True, exist_ok=True)

views = [
    ("front", 50, 70, 120),
    ("left", 70, 100, 140),
    ("right", 100, 70, 120),
]

uploaded_versions = {}

for name, r, g, b in views:
    png_bytes = make_solid_png(128, 128, r, g, b)
    img_path = tmp_dir / f"linmo_{name}.png"
    img_path.write_bytes(png_bytes)

    media = media_service.import_file(
        PROJECT_ID,
        str(img_path),
        purpose="ASSET_REFERENCE",
        owner_type="STORY_ASSET",
        owner_id=LINMO_ID,
        media_kind="IMAGE",
        stage="REFERENCE",
    )
    v_id = media["media_version_id"]
    # Authorize media version for project
    workspace_service.authorize_media_version(PROJECT_ID, v_id)
    uploaded_versions[name] = v_id
    print(f"Uploaded and authorized {name}: {v_id}")

# 2. Get or create Pack
packs = pack_service.list_packs(LINMO_ID)
if packs:
    pack = packs[0]
    pack_id = pack["id"]
else:
    pack = pack_service.create_pack(
        project_id=PROJECT_ID,
        story_asset_id=LINMO_ID,
        code="IP_LINMO_HERO_V1",
        name="林默 基础三视图身份包",
        description="包含正面、左侧与右侧三视图参考",
    )
    pack_id = pack["id"]

print("Pack ID:", pack_id)

# 3. Create version draft
version = pack_service.create_version_draft(pack_id)
version_id = version["id"]
print("Created Version Draft ID:", version_id)

# 4. Set slots: FRONT, LEFT, RIGHT
for kind, v_id in [("FRONT", uploaded_versions["front"]), ("LEFT", uploaded_versions["left"]), ("RIGHT", uploaded_versions["right"])]:
    pack_service.set_version_slot(version_id, kind, v_id)
    print(f"Set slot {kind} -> {v_id}")

# 5. Approve Version
approved = pack_service.approve_pack_version(version_id, comment="人工审阅通过：FRONT / LEFT / RIGHT 三视角齐全且特征严格一致")
print(f"Approved Version {version_id} status:", approved["status"])

# 6. Bind to Shot
binding = pack_service.bind_shot_identity_pack(SHOT_ID, LINMO_ID, version_id)
print(f"Bound to shot {SHOT_ID}:", binding)

print("ALL IDENTITY PACK STEPS SUCCEEDED!")
