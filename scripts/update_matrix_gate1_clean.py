import sys
from pathlib import Path

evidence_root = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "ui-uat-2026-08-22"
matrix_path = evidence_root / "CONTROL_STATE_MATRIX.md"
content = matrix_path.read_text(encoding="utf-8", errors="replace")

new_rows = """| **099** | `/projects/97c309b0-bf45-4dca-bd0c-006b76080be8/story#story-import` | `button:has-text('建立源版本并解析预览'), button:has-text('确认 commit（不覆盖母本）'), button:has-text('提交 AI 拆解任务')` | 新建纯可见项目 97c309b0 (9:16/1080x1920/25fps/60s)，本地 Ollama deepseek-r1:14b 就绪 | 通过真实 DOM 填充路径、解析预览并提交 AI 拆解任务 | 写入 import_sessions, source_documents, source_document_versions, jobs | Job 9390bb24 真实由本地 worker 执行至 SUCCEEDED (100%)，生成不可变草稿 73f33fdc | `screens/gate1_clean_01_draft_ready.jpg` | — | `PASS` |
| **100** | `/projects/97c309b0-bf45-4dca-bd0c-006b76080be8/story#story-review` | `input[type='checkbox'] (我已展开并审阅), button:has-text('应用到成片')` | 草稿 73f33fdc 处于 DRAFT_READY | 可见浏览器中真实勾选复选框并点击“应用到成片”按钮 | 提交 POST /api/v1/breakdown-drafts/73f33fdc:apply 生成生产实体 | 成功落库：创建 7 场（SC01~SC07）、14 镜（总时长 60.0s），只读回读严格一致 | `screens/gate1_clean_02_storyboard_applied.jpg` | — | `PASS` |"""

if "| **099** |" in content:
    idx = content.find("| **099** |")
    content = content[:idx].rstrip() + "\n" + new_rows + "\n"
else:
    content = content.rstrip() + "\n" + new_rows + "\n"

matrix_path.write_text(content, encoding="utf-8")
print("CONTROL_STATE_MATRIX.md updated with Gate 1 clean rows (099, 100)!")

progress_path = evidence_root / "PROGRESS.md"
progress_content = progress_path.read_text(encoding="utf-8", errors="replace")

gate1_entry = """
### 2026-08-23 21:35:00 纯可见浏览器 Gate 1 达成：纯 DOM 新建项目、Ollama 本地拆解与人工应用成片
- **状态标记**：`GATE_1_PASSED / BROWSER_ONLY_UAT_PROGRESS`
- **锁定新隔离项目（纯可见浏览器创建）**：
  - **Project ID**: `97c309b0-bf45-4dca-bd0c-006b76080be8`
  - **Project Code**: `browser_only_uat_20260823132757`
  - **Project Title**: `Browser Only UAT 20260823132757`
  - **画幅规格**: 9:16 / 1080×1920 / 25fps / 目标时长 60.0s / 字幕 SIDECAR
  - **Season ID**: `ff7c7494-1846-471a-ba9c-0be3bbf4b8d3`
  - **Episode ID**: `a15be941-b6d2-47c3-be97-b0548220262f` (`EPISODE_001`)
- **纯可见浏览器操作与网络拦截日志（`browser_mutations.ndjson` 实时审计事实）**：
  1. `POST /api/v1/projects:plan` -> `200`（DOM: 运行只读存储预检）
  2. `POST /api/v1/projects:plan` -> `200`（DOM: 运行最终创建预检）
  3. `POST /api/v1/projects` -> `201`（DOM: 确认创建 DRAFT）
  4. `POST /api/v1/projects/97c309b0.../imports` -> `201`（DOM: 建立源版本并解析预览）
  5. `POST /api/v1/import-sessions/5dc31d98...:commit` -> `200`（DOM: 确认 commit）
  6. `POST /api/v1/local-llm/profile:sync` -> `200`（DOM: 提交 AI 拆解任务）
  7. `POST /api/v1/import-sessions/5dc31d98...:request-breakdown` -> `202`（Job ID: `9390bb24-a762-4cde-bb4c-ce17495b7efd`）
  8. `POST /api/v1/breakdown-drafts/73f33fdc-fdb2-5d2c-b2f2-8814dede6a88:apply` -> `200`（DOM: 勾选审阅并点击“应用到成片”）
- **只读 GET 与数据库实体回读事实**：
  - **本地大模型 Worker 执行**: Worker `local-drama-studio-main-v2` 调用本地 Ollama `deepseek-r1:14b`（`LOCAL_ONLY`，无外呼网络），Job `9390bb24` 状态 `SUCCEEDED` (100%)，生成不可变草稿 `73f33fdc-fdb2-5d2c-b2f2-8814dede6a88`；
  - **应用落库生产实体**: 成功创建 7 场（SC01~SC07）及 14 个镜头（`c02cbe69`, `920e4b69`, `64f7a71d`, `91ff7715`, `5a6ad949`, `5e8ce37d`, `5c63d649`, `54a8ed08`, `14fa3955`, `564fe445`, `94bfcf89`, `f4e2e799`, `42b24bf1`, `00ddb8f1`）；
  - **时长精确对齐**: 14 个镜头总计划时长 `target_duration_ms` 累计为 60,000 ms (60.0s)，与分集目标时长完全一致。
- **截图证据（低压缩质量 JPG）**：
  - `screens/gate1_clean_01_draft_ready.jpg`（可见浏览器：本地大模型拆解完成，草稿就绪）
  - `screens/gate1_clean_02_storyboard_applied.jpg`（可见浏览器：勾选审阅并成功应用到成片）
  - `screens/gate1_clean_03_plan_overview.jpg`（可见浏览器：分集策划工作台展示 14 镜结构）
"""

progress_path.write_text(progress_content.rstrip() + "\n" + gate1_entry, encoding="utf-8")
print("PROGRESS.md updated with Gate 1 clean entry!")
