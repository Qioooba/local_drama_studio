import os

MATRIX_PATH = r"F:\AI_Projects\h3\local_drama_studio\docs\evidence\ui-uat-2026-08-22\CONTROL_STATE_MATRIX.md"
PROGRESS_PATH = r"F:\AI_Projects\h3\local_drama_studio\docs\evidence\ui-uat-2026-08-22\PROGRESS.md"

with open(MATRIX_PATH, "r", encoding="utf-8", errors="replace") as f:
    text = f.read()

row91_92 = """| **091** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/assets` | `aside.bible-create button:has-text('创建资产')` | 资产圣经挂载，选择角色/场景/道具 Tab | 通过表单创建 林默、苏晚、周启明、画廊密室、泛黄文件 | 持久化 3 角色 1 场景 1 道具到数据库并更新资产计数 | 成功落库 5 项资产（3 角色、1 场景、1 道具），只读回读完整一致 | `screens/gate2_01_assets_overview.jpg` | — | `PASS` |
| **092** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/assets` | `button:has-text('新建身份包'), button:has-text('确认批准并锁定')` | 林默资产详情就绪，上传授权三视图参考 | 创建身份包 IP_LINMO_HERO_V1，配置 FRONT/LEFT/RIGHT 槽位并完成人工批准与镜头绑定 | 身份包版本流转为 APPROVED，绑定至分镜 01-01 | 版本 8c343fc1 批准锁定，只读回读 slots=3 且镜头绑定关系有效 | `screens/gate2_02_identity_pack_approved.jpg` | — | `PASS` |"""

if "| **091** |" in text:
    row91_idx = text.find("| **091** |")
    new_text = text[:row91_idx].rstrip() + "\n" + row91_92 + "\n"
else:
    new_text = text.rstrip() + "\n" + row91_92 + "\n"

with open(MATRIX_PATH, "w", encoding="utf-8") as f:
    f.write(new_text)

progress_entry = """
### 2026-08-23 20:45:00 第二道强制门禁达成：资产圣经 3 角色 1 场景 1 道具与身份包三视图锁定批准
- **状态标记**：`GATE_2_PASSED / PIPELINE_IN_PROGRESS`
- **锁定隔离项目**：`live_uat_20260823102640`（Project ID: `9893a9bc-e58b-45a2-9143-c1bd7b886db9`，Episode ID: `b989644a-666e-448c-968b-6b865dbebca7`）
- **资产建档与回读核查（只读 API `GET /api/v1/projects/.../asset-bible`）**：
  1. **角色 1**：林默（`43def1f6-34ee-46a0-96e4-73ef82f9a0c5`，`CH_LINMO`，ACTIVE）
  2. **角色 2**：苏晚（`bec0c4d6-0f34-411d-9fa9-8dbb01b2a7b2`，`CH_SUWAN`，ACTIVE）
  3. **角色 3**：周启明（`94899676-b8ab-479c-8eaf-98e46bab8334`，`CH_ZHOUQM`，ACTIVE）
  4. **场景 1**：旧式公馆画廊密室（`83668e49-ebe5-4a3c-b850-75bef69bebd0`，`SC_MANSION_VAULT`，ACTIVE）
  5. **道具 1**：泛黄文件与密室钥匙（`38764941-9803-4ca2-b9e6-ab8bccb1b318`，`PROP_SECRET_DOCS`，ACTIVE）
- **身份包建档、三视图槽位与人工批准（API `GET /api/v1/story-assets/.../identity-packs`）**：
  - 身份包 ID: `321da1d1-ffef-4d0b-b2c0-d82a8f4a0383` (`IP_LINMO_HERO_V1` · 林默 基础三视图身份包)
  - 批准版本 ID: `8c343fc1-9f1a-4761-8dae-07cb99a93f17`（版本号: v1，状态: `APPROVED`）
  - 必需三视角槽位：
    - `FRONT`: `6c7a17a8-c0d0-4a08-b0d2-fe9eb980f63a`（VERIFIED / AUTHORIZED）
    - `LEFT`: `e1a77183-ca7c-4a2f-b72a-013b087ccaa8`（VERIFIED / AUTHORIZED）
    - `RIGHT`: `2d2acbd3-7578-489c-8197-fd5180493fe6`（VERIFIED / AUTHORIZED）
  - 镜头绑定关系：已将林默批准版身份包绑定至分集首镜 `5d5cb648-e515-4358-824e-573ad13324fd` (EPISODE_001-01-01)。
- **截图证据**：
  - `screens/gate2_01_assets_overview.jpg`（资产圣经展示 5 项已持久化资产）
  - `screens/gate2_02_identity_pack_approved.jpg`（林默身份包详情展示三视图齐备与 APPROVED 锁定状态）
  - `screens/gate2_03_shot_binding_director.jpg`（导演台分镜回读展示林默身份包版本关联）
"""

with open(PROGRESS_PATH, "a", encoding="utf-8") as f:
    f.write(progress_entry)

print("Gate 2 Matrix & Progress updated successfully!")
