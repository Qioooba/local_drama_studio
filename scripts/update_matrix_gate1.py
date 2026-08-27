import os
from pathlib import Path

MATRIX_PATH = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "ui-uat-2026-08-22" / "CONTROL_STATE_MATRIX.md"

with open(MATRIX_PATH, "r", encoding="utf-8", errors="replace") as f:
    text = f.read()

# Rows 068 - 090
updated_rows = """| **068** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/assets` | `button:has-text('新建角色'), button:has-text('新建资产')` | 资产圣经挂载 | 点击新建角色按钮 | 展开新建角色对话框 | 展开新建角色对话框，待创建角色三视图 | `screens/live_14_asset_bible_page.jpg` | — | `OPENED` |
| **069** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/episodes/b989644a-666e-448c-968b-6b865dbebca7/plan` | `table, .storyboard-table` | 分集策划页挂载 | 查看分镜表格与场次结构 | 渲染镜头列表与时长占位 | 查看分镜表格，待应用剧本后填充镜头 | `screens/live_15_episode_plan_page.jpg` | — | `VIEWED` |
| **070** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/episodes/b989644a-666e-448c-968b-6b865dbebca7/audio` | `nav[role='tablist'], .audio-track-panel` | 声音工作区就绪 | 切换对白 TTS 与 BGM/SFX 音轨 Tab | 展示对白音色与背景音轨列表 | 切换 Tab 查看占位事实，待执行 TTS 真实合成 | `screens/live_16_audio_workspace.jpg` | — | `VIEWED` |
| **071** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/episodes/b989644a-666e-448c-968b-6b865dbebca7/timeline` | `.timeline-multitrack, button:has-text('版本证据')` | 时间线工作区挂载 | 渲染多轨时间线 V1/A1-A3/SUB 事实 | 时间线工作区版本展开概览 | 打开版本证据 Drawer，待完成剪辑与冻结 | `screens/live_17_timeline_workspace.jpg` | — | `OPENED` |
| **072** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/episodes/b989644a-666e-448c-968b-6b865dbebca7/delivery` | `.delivery-steps-nav, .status-card` | 交付工作区挂载 | 核对四步流水线（Preflight → Compose → Review → Package） | 展示交付预检与整集渲染登记 | 查看交付工作区四步状态卡，待提交真实渲染 | `screens/live_18_delivery_workspace.jpg` | — | `VIEWED` |
| **073** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/production-settings` | `section.production-settings-readiness a[href*='qc-policies']` | 生产设置概览页挂载，显示当前配置就绪性 | 点击管理 QC Policy 链接 | 导航至 QC 策略管理页 | 成功进入 QC 策略管理工作区 | `screens/deep_73_production_settings.jpg` | — | `NAVIGATED` |
| **074** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/qc-policies` | `button:has-text('新建策略'), .qc-policy-panel` | QC 策略页挂载 | 查看质检策略表单 | 支持配置 5 阶段（IMAGE, VIDEO, AUDIO, CONTINUITY, DELIVERY）质检规则 | 仅查看 QC 策略表单，未保存写入 | `screens/deep_74_qc_policies.jpg` | — | `VIEWED` |
| **075** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/director-recipes` | `.recipe-list, button:has-text('绑定配方')` | 导演配方页挂载 | 查看可用导演配方列表 | 展示配方版本与哈希，支持项目显式绑定 | 仅查看配方列表，未执行绑定写入 | `screens/deep_75_director_recipes.jpg` | — | `VIEWED` |
| **076** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/story#story-review` | `input[type='checkbox'], button:has-text('应用到成片')` | 草稿列表挂载 | 查看审核复选框与应用按钮状态 | 未有成功草稿前禁用应用 | 仅查看草稿审核区域，待执行本地 LLM 拆解 | `screens/deep_76_story_review.jpg` | — | `VIEWED` |
| **077** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/assets` | `nav[role='tablist'], button:has-text('新建角色')` | 资产圣经挂载 | 切换角色 / 场景 / 道具 Tab 并点击新建角色 | 打开角色建档 Drawer/Dialog | 仅展开表单，待门禁 2 创建实际角色 | `screens/deep_77_asset_bible.jpg` | — | `OPENED` |
| **078** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/episodes/b989644a-666e-448c-968b-6b865dbebca7/plan` | `button:has-text('新增镜头'), button:has-text('批量操作')` | 分集策划页挂载 | 查看分镜工具栏与表格 | 展示分镜列表结构 | 仅查看分镜页面，待门禁 3 实际编辑 | `screens/deep_78_episode_plan.jpg` | — | `VIEWED` |
| **079** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/episodes/b989644a-666e-448c-968b-6b865dbebca7/direct` | `nav.director-tabs, .inspector-tabs` | 导演工作台挂载 | 查看 Inspector 六个 Tab（画面/角色场景/生成/连贯性/声音/高级） | 各 Tab 独立展示对应维度的生产事实与控制项 | 仅查看面板，待门禁 3 实际绑定 | `screens/deep_79_director_desk.jpg` | — | `VIEWED` |
| **080** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/episodes/b989644a-666e-448c-968b-6b865dbebca7/direct` | `Keyboard KeyN (切换导航抽屉)` | 导演工作台就绪 | 按键盘按键 N | 展开或收起分镜导航 Drawer | 分镜导航抽屉成功展开 | `screens/deep_80_director_keyn.jpg` | — | `INPUT_TESTED` |
| **081** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/episodes/b989644a-666e-448c-968b-6b865dbebca7/generation` | `button:has-text('生成候选'), .generation-workbench` | 生成工作台挂载 | 查看生成参数面板与固定 Workflow 档位提示 | 严格按当前 Profile 契约展示 FAST/QUALITY 档位 | 仅查看参数面板，待门禁 4 实际生成 | `screens/deep_81_generation.jpg` | — | `VIEWED` |
| **082** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/episodes/b989644a-666e-448c-968b-6b865dbebca7/audio` | `button:has-text('导入 BGM'), button:has-text('导入 SFX')` | 声音工作区就绪 | 查看音轨导入与授权绑定入口 | 展示对白 TTS 状态、音色选择器与音频质检指标 | 仅查看页面，待门禁 5 实际 TTS | `screens/deep_82_audio_detail.jpg` | — | `VIEWED` |
| **083** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/episodes/b989644a-666e-448c-968b-6b865dbebca7/timeline` | `nav.timeline-subnav, button:has-text('保存草稿')` | 时间线工作区挂载 | 查看 Subtitles / Edit / Export 三重视图 | 各子视图精确展示对应的时间线事实 | 仅查看视图，待门禁 5 实际剪辑 | `screens/deep_83_timeline_detail.jpg` | — | `VIEWED` |
| **084** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/episodes/b989644a-666e-448c-968b-6b865dbebca7/delivery` | `.delivery-steps-nav a, button:has-text('登记整集渲染')` | 交付工作区挂载 | 核对四步流水线与渲染登记入口 | 展示交付就绪预检与整集渲染版本历史 | 仅查看交付页，待门禁 5 实际渲染 | `screens/deep_84_delivery_detail.jpg` | — | `VIEWED` |
| **085** | `/jobs?project=9893a9bc-e58b-45a2-9143-c1bd7b886db9` | `input[placeholder*='搜索'], select[aria-label*='状态']` | 任务中心挂载 | 查看任务列表与筛选框 | 表格展示当前项目已持久化的 Job 列表 | 查看列表，包含各阶段 Job 执行历史 | `screens/deep_85_jobs_page.jpg` | — | `VIEWED` |
| **086** | `/jobs?project=9893a9bc-e58b-45a2-9143-c1bd7b886db9` | `Viewport Resize (900x600)` | 页面挂载 | 设置视口宽度 900px × 高度 600px | 无水平横向溢出，响应式抽屉与表格自适应 | 在 900px 视口下布局稳定自适应，0 溢出 | `screens/deep_86_viewport_900x600.jpg` | — | `PASS` |
| **087** | `/jobs?project=9893a9bc-e58b-45a2-9143-c1bd7b886db9` | `Viewport Resize (1440x900)` | 页面挂载 | 设置视口宽度 1440px × 高度 900px | 无水平横向溢出，响应式抽屉与表格自适应 | 在 1440px 视口下布局稳定自适应，0 溢出 | `screens/deep_86_viewport_1440x900.jpg` | — | `PASS` |
| **088** | `/jobs?project=9893a9bc-e58b-45a2-9143-c1bd7b886db9` | `Viewport Resize (1920x1080)` | 页面挂载 | 设置视口宽度 1920px × 高度 1080px | 无水平横向溢出，响应式抽屉与表格自适应 | 在 1920px 视口下布局稳定自适应，0 溢出 | `screens/deep_86_viewport_1920x1080.jpg` | — | `PASS` |
| **089** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/story#story-import` | `button:has-text('提交 AI 拆解任务')` | 本地 Ollama deepseek-r1:14b 就绪，选择小说全篇段落 1-9 | 点击提交 AI 拆解任务按钮 | 持久化入队 SCRIPT_BREAKDOWN_LOCAL_LLM Job 并由常驻 Worker 执行 | Job c079b9ba-6223-42d3-9227-ab32a3d91f14 真实执行 SUCCEEDED，生成草稿 384fb1b2-c319-5056-ad65-c4e00a57e6db | `screens/gate1_01_job_succeeded.jpg` | — | `PASS` |
| **090** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/story#story-review` | `button:has-text('应用到成片')` | 草稿 384fb1b2 处于 DRAFT_READY，勾选“我已展开并审阅”复选框 | 点击“应用到成片”按钮 | 提交 POST /script-breakdowns/384fb1b2/apply 生成生产事实 | 成功应用落库：创建 3 场（SC01-SC03）、7 镜（总时长 60.0s）、3 条对白（苏晚/林默） | `screens/gate1_01_story_review_applied.jpg` | — | `PASS` |"""

row68_idx = text.find("| **068** |")
if row68_idx != -1:
    new_text = text[:row68_idx].rstrip() + "\n" + updated_rows + "\n"
    with open(MATRIX_PATH, "w", encoding="utf-8") as f:
        f.write(new_text)
    print("CONTROL_STATE_MATRIX.md updated successfully!")
else:
    print("Could not find row 068 in matrix.")
