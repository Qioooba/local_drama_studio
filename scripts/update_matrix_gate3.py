import os
from pathlib import Path

EVIDENCE_ROOT = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "ui-uat-2026-08-22"
MATRIX_PATH = EVIDENCE_ROOT / "CONTROL_STATE_MATRIX.md"
PROGRESS_PATH = EVIDENCE_ROOT / "PROGRESS.md"

with open(MATRIX_PATH, "r", encoding="utf-8", errors="replace") as f:
    text = f.read()

row93 = """| **093** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/episodes/b989644a-666e-448c-968b-6b865dbebca7/direct` | `button:has-text('采用为 KEYFRAME'), .director-take-adoption` | 镜头 01-01 绑定林默身份包，首帧候选生成 | 执行 KEYFRAME 首帧采用与人工审阅锁定 | 持久化 selections 记录，状态标记为 SELECTED | 候选 1654ad35 成功采用为 KEYFRAME，只读回读 selection_type=KEYFRAME | `screens/gate3_02_director_desk_keyframe_selected.jpg` | — | `PASS` |"""

if "| **093** |" in text:
    row93_idx = text.find("| **093** |")
    new_text = text[:row93_idx].rstrip() + "\n" + row93 + "\n"
else:
    new_text = text.rstrip() + "\n" + row93 + "\n"

with open(MATRIX_PATH, "w", encoding="utf-8") as f:
    f.write(new_text)

progress_entry = """
### 2026-08-23 20:50:00 第三道强制门禁达成：分镜策划确认、导演台首帧质检与人工采用
- **状态标记**：`GATE_3_PASSED / PIPELINE_IN_PROGRESS`
- **锁定隔离项目**：`live_uat_20260823102640`（Project ID: `9893a9bc-e58b-45a2-9143-c1bd7b886db9`，Episode ID: `b989644a-666e-448c-968b-6b865dbebca7`）
- **导演台首帧采用与质检事实（只读 API `GET /api/v1/projects/episodes/.../storyboard` & `GET /api/v1/projects/.../reviews/inbox`）**：
  - 目标镜头: `5d5cb648-e515-4358-824e-573ad13324fd` (`EPISODE_001-01-01`)
  - 首帧候选 ID: `1654ad35-012e-4f65-874a-e6da172fc177` (stage: `KEYFRAME`, purpose: `KEYFRAME`, 128x128 PNG)
  - 采用记录 ID: `07d2a83b-0923-4dda-b5d9-e10d47b2f9dc` (selection_type: `KEYFRAME`, status: `SELECTED`)
  - 角色关联: 林默标准三视图身份包已锁定并在导演台 Inspector 资产 Tab 稳定回读。
- **截图证据**：
  - `screens/gate3_01_plan_overview.jpg`（分集策划 7 镜结构）
  - `screens/gate3_02_director_desk_keyframe_selected.jpg`（导演台展示首帧已采用）
  - `screens/gate3_03_review_inbox.jpg`（审核中心收件箱）
"""

with open(PROGRESS_PATH, "a", encoding="utf-8") as f:
    f.write(progress_entry)

print("Gate 3 Matrix & Progress updated successfully!")
