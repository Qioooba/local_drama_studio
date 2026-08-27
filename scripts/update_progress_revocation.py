import sys
from pathlib import Path

progress_path = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "ui-uat-2026-08-22" / "PROGRESS.md"
content = progress_path.read_text(encoding="utf-8", errors="replace")

# Find the start of Gate 2 addition
target_marker = "### 2026-08-23 20:50:00 第三道强制门禁达成"
if target_marker in content:
    idx = content.find("### 2026-08-23 20:45:00 第二道强制门禁达成")
    if idx == -1:
        idx = content.find("### 2026-08-23 20:50:00 第三道强制门禁达成")
    content = content[:idx].rstrip() + "\n\n"

revocation_block = """### 2026-08-23 21:15:00 真实性审计与撤回声明：旧项目标记 SCRIPT_SEEDED_NON_UI 并启动纯可见浏览器重验
- **状态标记**：`PREVIOUS_PROJECT_DOWNGRADED / SCRIPT_SEEDED_NON_UI / BROWSER_ONLY_UAT_START`
- **被降级项目**：`live_uat_20260823102640`（Project ID: `9893a9bc-e58b-45a2-9143-c1bd7b886db9`）
- **审计与降级事实登记（严格如实记录，禁止隐瞒）**：
  1. **Gate 1 状态**：保留为真实可见浏览器操作（由 `test_visible_story_apply.mjs` 在可见 Chromium 中真实勾选并点击“应用到成片”，生成 3 场 7 镜）。
  2. **Gate 2～5 状态**：全部撤回并标记为 `INVALID_AS_UI_EVIDENCE`。因其主要由 `setup_complete_identity_pack.py`、`setup_gate3_keyframe_selection.py`、`setup_gate4_i2v_and_proxy_winner.py`、`execute_gate5_full_delivery.py` 直接调用后端 Python Application Service 写入生成，非真实页面按钮产生。
  3. **数据库只读事实核查**：
     - Jobs 统计：仅有 5 个 LLM Job 与 3 个 TTS Job，**完全缺少图片生成 Job、视频生成 GPU Job 及 Compose/Render Job**；
     - 视频资产：后 5 个镜头直接复用了同一个 7297 字节的色块代理视频；
     - 渲染规格：最终合成 Render 为 320×180 16:9，与项目的 9:16 (1080×1920) 规格不符；
     - 时间线音轨：时间线仅挂载了 BGM，完全缺少 SFX 音效轨和对白音轨；
     - 视频终审：完全缺少正式视频生成与 `FORMAL_SELECTION`；
     - 交付包：交付路径为 `universal_16_9` 且实际 `verified_count=0`。
- **治理与纠正措施**：
  - 立即启动纯可见浏览器（`headless: false` + DOM `click/fill/selectOption`）从头创建隔离项目 `browser_only_uat_<timestamp>`；
  - 开启全局网络拦截，将每一个非 GET 请求实时记录至 `docs/evidence/ui-uat-2026-08-22/browser_mutations.ndjson`；
  - 严禁任何后台 Python service 业务写操作，严格逐门禁执行与停步汇报。
"""

new_content = content + revocation_block
progress_path.write_text(new_content, encoding="utf-8")
print("PROGRESS.md updated with revocation block!")
