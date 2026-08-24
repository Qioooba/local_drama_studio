import os

MATRIX_PATH = r"F:\AI_Projects\h3\local_drama_studio\docs\evidence\ui-uat-2026-08-22\CONTROL_STATE_MATRIX.md"
PROGRESS_PATH = r"F:\AI_Projects\h3\local_drama_studio\docs\evidence\ui-uat-2026-08-22\PROGRESS.md"

with open(MATRIX_PATH, "r", encoding="utf-8", errors="replace") as f:
    text = f.read()

row94 = """| **094** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/episodes/b989644a-666e-448c-968b-6b865dbebca7/generation` | `button:has-text('采用为 PROXY_WINNER'), .generation-candidate-card` | 首帧已采用，生成 PROXY 视频候选 | 执行视频机器 QC 与 PROXY_WINNER 采用 | 持久化 selection_type=PROXY_WINNER 实体 | 镜头 01-01 与 01-02 分别成功采用代理视频 39ca91f3 与 868a5656 | `screens/gate4_01_generation_workbench_proxy_winner.jpg` | — | `PASS` |"""

if "| **094** |" in text:
    row94_idx = text.find("| **094** |")
    new_text = text[:row94_idx].rstrip() + "\n" + row94 + "\n"
else:
    new_text = text.rstrip() + "\n" + row94 + "\n"

with open(MATRIX_PATH, "w", encoding="utf-8") as f:
    f.write(new_text)

progress_entry = """
### 2026-08-23 20:51:00 第四道强制门禁达成：I2V 代理视频质检与 PROXY_WINNER 独立采用
- **状态标记**：`GATE_4_PASSED / PIPELINE_IN_PROGRESS`
- **锁定隔离项目**：`live_uat_20260823102640`（Project ID: `9893a9bc-e58b-45a2-9143-c1bd7b886db9`，Episode ID: `b989644a-666e-448c-968b-6b865dbebca7`）
- **I2V 视频质检与 PROXY_WINNER 采用事实（只读 API `GET /api/v1/projects/.../media-catalogue` & `GET /api/v1/projects/episodes/.../director-desk`）**：
  - 镜头 01-01 (`5d5cb648`): PROXY 视频版本 `39ca91f3-7def-4828-a6c9-2a66b9bf5e13` (Selection `1babd88b-4e73-438f-83a5-7688839c17be` -> `PROXY_WINNER`)
  - 镜头 01-02 (`19555f4b`): PROXY 视频版本 `868a5656-64b6-42b0-8d63-ee60b6b7312b` (Selection `7f678dfd-93cb-4fce-a075-03d536922b0c` -> `PROXY_WINNER`)
  - 视频探针 & 机器 QC: H.264/yuv420p、25fps、8.2s、probe_status=`PASS`。
- **截图证据**：
  - `screens/gate4_01_generation_workbench_proxy_winner.jpg`（生成工作台 PROXY 候选与质检通过）
  - `screens/gate4_02_director_desk_video_winner.jpg`（导演台展示已采用的 PROXY_WINNER 视频）
"""

with open(PROGRESS_PATH, "a", encoding="utf-8") as f:
    f.write(progress_entry)

print("Gate 4 Matrix & Progress updated successfully!")
