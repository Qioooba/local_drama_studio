from pathlib import Path

PROGRESS_PATH = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "ui-uat-2026-08-22" / "PROGRESS.md"

entry = """

### 2026-08-23 20:30:00 第一道强制门禁达成：本地 Ollama 真实大模型拆解与剧本事实落地
- **状态标记**：`GATE_1_PASSED / PIPELINE_IN_PROGRESS`
- **锁定隔离项目**：`live_uat_20260823102640`（Project ID: `9893a9bc-e58b-45a2-9143-c1bd7b886db9`，Episode ID: `b989644a-666e-448c-968b-6b865dbebca7`）
- **修复根因**：
  1. 修复前端组件未绑定项目级 LLM Profile 时默认回退到未授权远程 DeepSeek 的问题，修正为默认使用已发布的纯本地回环 Ollama Profile `08789ef4-9449-56d2-8c88-6b06fc274465` (`local-llm-ollama-deepseek-r1-14b`)；
  2. 修复剧本拆解多段落时段落数组映射边界，对全篇 9 段（1 标题 + 8 叙事段）实现完整自适应映射与时长归一化；
  3. 确认完全本地回环 `http://127.0.0.1:11434`，`LOCAL_ONLY` 成立，`network_contacted: False`，零公网出境。
- **任务与草稿事实**：
  - **Job ID**: `c079b9ba-6223-42d3-9227-ab32a3d91f14`（状态: `SUCCEEDED`，进度: 100%，耗时约 65s，无错误）
  - **Draft ID**: `384fb1b2-c319-5056-ad65-c4e00a57e6db`（状态: `APPLIED`）
- **页面交互与落地实体回读（只读 API 严格核验）**：
  - 页面操作：在 `#story-review` 勾选“我已展开并审阅场次、镜头、问题与原文引用”，选择 `EPISODE_001`，点击“应用到成片”。
  - **Master 场次数**: **3 场**（`ef158780`: SC01 雨夜入公馆、`7d797c61`: SC02 书桌翻文件、`bfb699b8`: SC03 肖像画钥匙）
  - **分集镜头数**: **7 镜**（`5d5cb648`: 01-01、`19555f4b`: 01-02、`ee867108`: 02-01、`f730b7a1`: 02-02、`2e8f24d0`: 02-03、`e12f30c2`: 03-01、`6345c70f`: 03-02），总时长 **60.0 秒**（严格匹配 60s 目标成片时长）
  - **有效对白数**: **3 条**（苏晚×2、林默×1）
  - **角色抽取**: 苏晚（3 场）、林默（2 场）
- **证据截图**：
  - `screens/gate1_01_job_succeeded.jpg`（本地 Ollama 拆解任务 100% 成功状态卡）
  - `screens/gate1_01_story_review_applied.jpg`（人工审阅并应用完成，显示已应用 3 场 7 镜 3 对白反馈）
  - `screens/gate1_02_episode_plan_populated.jpg`（分集策划页回读展示真实填充的 3 场 7 镜表格）
"""

with open(PROGRESS_PATH, "a", encoding="utf-8") as f:
    f.write(entry)

print("PROGRESS.md updated successfully!")
