# LocalDrama Studio — 全页面多分辨率与多主题 UI/UX 自动化测试审核报告

**审计执行时间**: 2026-09-02T16:20:06.633Z 至 2026-09-02T16:23:23.014Z
**被测前端地址**: `http://127.0.0.1:5173`
**测试执行引擎**: Playwright Chromium Headless Architecture

## 1. 核心测试看板 (Executive Summary)

| 审计指标 | 统计数值 | 状态说明 |
| :--- | :--- | :--- |
| **被测工作区总数** | **24 个** | 涵盖全局、项目、故事改编、导演工作台、后期及系统诊断 |
| **视口分辨率档位** | **4 档** | 1920x1080 (桌面)、1440x900 (笔电)、1024x768 (平板)、375x812 (移动端) |
| **主题色彩模式** | **2 种** | 暗黑工控机主题 (Native Dark) + 浅色高对比仿真模式 (Light Simulation) |
| **捕获核验截图总数** | **144 张** | 高清视觉快照，覆盖各分辨率首屏与展开态 |
| **交互按键审计数** | **84 个** | 100% 校验热区、Hover、Disabled 态与非破坏性点击 |
| **输入框/文本域审计数** | **54 个** | 100% 注入边界测试字符串并检验回显与 Focus 轮廓 |
| **下拉框/选项审计数** | **23 个** | 遍历选项值、状态与激活事件 |
| **二级 Tab 切换审计数** | **15 个** | 遍历各子视图切换逻辑 |

### 缺陷严重度分布

- 🔴 **致命缺陷 (Critical)**: **0** 个 (页面崩溃、脚本超时、关键主功能失灵)
- 🟠 **严重缺陷 (High)**: **0** 个 (窄屏横向巨幅溢出导致截断、浅色白底白字失明)
- 🟡 **中度缺陷 (Medium)**: **42** 个 (对比度偏低、图片资源破损、弹窗边缘贴边)
- 🟢 **轻微建议 (Low)**: **7** 个 (触摸热区小于 32px、行内元素轻微错位、文本微量截断)

---

## 2. 缺陷明细与分类归因 (Defect Inventory)

| 严重度 | 缺陷类型 | 影响页面 | 详细描述与诊断结论 |
| :--- | :--- | :--- | :--- |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 全局首页 (Home) | 在浅色模式下检测到 24 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 全局首页 (Home) | 在浅色模式下检测到 23 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟢 轻微 | `TEXT_CLIPPED` | 项目管理 (Projects) | 在 桌面 (1920x1080) 发现 2 处文本截断 (示例: "zhao_gu_deng_duan_dao_duan_zhe", "zhao_gu_deng_quan_liu_cheng_sh") |
| 🟢 轻微 | `TEXT_CLIPPED` | 项目管理 (Projects) | 在 笔记本 (1440x900) 发现 2 处文本截断 (示例: "zhao_gu_deng_duan_dao_duan_zhe", "zhao_gu_deng_quan_liu_cheng_sh") |
| 🟢 轻微 | `TEXT_CLIPPED` | 项目管理 (Projects) | 在 平板 (1024x768) 发现 2 处文本截断 (示例: "zhao_gu_deng_duan_dao_duan_zhe", "zhao_gu_deng_quan_liu_cheng_sh") |
| 🟢 轻微 | `TEXT_CLIPPED` | 项目管理 (Projects) | 在 移动端 (375x812) 发现 2 处文本截断 (示例: "zhao_gu_deng_duan_dao_duan_zhe", "zhao_gu_deng_quan_liu_cheng_sh") |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 极速生成台 (Quick Create) | 在浅色模式下检测到 72 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 极速生成台 (Quick Create) | 在浅色模式下检测到 71 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 系统模型库 (Models) | 在浅色模式下检测到 57 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 系统模型库 (Models) | 在浅色模式下检测到 56 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 系统任务队列 (Jobs) | 在浅色模式下检测到 15 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 系统任务队列 (Jobs) | 在浅色模式下检测到 14 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 系统诊断面板 (Diagnostics) | 在浅色模式下检测到 12 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 系统诊断面板 (Diagnostics) | 在浅色模式下检测到 11 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 系统工作流 (Workflows) | 在浅色模式下检测到 54 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 系统工作流 (Workflows) | 在浅色模式下检测到 53 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 项目总览看板 (Project Home) | 在浅色模式下检测到 26 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 项目总览看板 (Project Home) | 在浅色模式下检测到 24 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 故事工作区 (Story) | 在浅色模式下检测到 21 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 故事工作区 (Story) | 在浅色模式下检测到 19 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 改编策划列表 (Story Plans) | 在浅色模式下检测到 24 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 改编策划列表 (Story Plans) | 在浅色模式下检测到 22 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 改编策划详情 (Plan Workspace) | 在浅色模式下检测到 12 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 资产圣经 (Asset Bible) | 在浅色模式下检测到 15 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 资产圣经 (Asset Bible) | 在浅色模式下检测到 13 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 生产设置 (Settings Production) | 在浅色模式下检测到 26 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 生产设置 (Settings Production) | 在浅色模式下检测到 24 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 导演配方设置 (Settings Directing) | 在浅色模式下检测到 26 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 导演配方设置 (Settings Directing) | 在浅色模式下检测到 24 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 质检策略设置 (Settings Quality) | 在浅色模式下检测到 55 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 质检策略设置 (Settings Quality) | 在浅色模式下检测到 53 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 视觉实验室列表 (Visual Labs) | 在浅色模式下检测到 11 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 分集策划台 (Episode Plan) | 在浅色模式下检测到 20 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 分集策划台 (Episode Plan) | 在浅色模式下检测到 19 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 导演镜头工作台 (Director Desk) | 在浅色模式下检测到 13 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 导演镜头工作台 (Director Desk) | 在浅色模式下检测到 12 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟢 轻微 | `TEXT_CLIPPED` | 镜头精修视图 (Shot Detail) | 在 移动端 (375x812) 发现 1 处文本截断 (示例: "镜头 cd0a399d-0cee-4d0c-98cf-d16") |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 镜头精修视图 (Shot Detail) | 在浅色模式下检测到 24 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 镜头精修视图 (Shot Detail) | 在浅色模式下检测到 13 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 后期审核中心 (Post Review) | 在浅色模式下检测到 15 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 后期审核中心 (Post Review) | 在浅色模式下检测到 14 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 声音工作区 (Post Audio) | 在浅色模式下检测到 14 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 声音工作区 (Post Audio) | 在浅色模式下检测到 13 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟢 轻微 | `TEXT_CLIPPED` | 时间线剪辑台 (Timeline) | 在 笔记本 (1440x900) 发现 1 处文本截断 (示例: "PROFILE_I2V_EVIDENCE_001") |
| 🟢 轻微 | `TEXT_CLIPPED` | 时间线剪辑台 (Timeline) | 在 平板 (1024x768) 发现 1 处文本截断 (示例: "PROFILE_I2V_EVIDENCE_001") |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 时间线剪辑台 (Timeline) | 在浅色模式下检测到 25 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 时间线剪辑台 (Timeline) | 在浅色模式下检测到 24 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 成片交付中心 (Delivery) | 在浅色模式下检测到 15 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |
| 🟡 中度 | `LOW_CONTRAST_RATIO` | 成片交付中心 (Delivery) | 在浅色模式下检测到 14 处文本色彩对比度低于 WCAG AA 3.0:1 阈值 |

---

## 3. 分页面详细审计结果 (Page-by-Page Breakdown)

### 全局首页 (Home) (`/`)

- **交互统计**: 按钮 `1` 个，输入框 `0` 个，下拉框 `1` 个，Tab `0` 个
- **缺陷数**: `2` 处

**页面缺陷清单**:
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 24 处文本色彩对比度低于 WCAG AA 3.0:1 阈值
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 23 处文本色彩对比度低于 WCAG AA 3.0:1 阈值

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P01_home\P01_home_desktop_1080p_dark.png](screenshots\P01_home\P01_home_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P01_home\P01_home_laptop_900p_dark.png](screenshots\P01_home\P01_home_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P01_home\P01_home_tablet_768p_dark.png](screenshots\P01_home\P01_home_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P01_home\P01_home_mobile_375p_dark.png](screenshots\P01_home\P01_home_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P01_home\P01_home_desktop_1080p_light.png](screenshots\P01_home\P01_home_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P01_home\P01_home_mobile_375p_light.png](screenshots\P01_home\P01_home_mobile_375p_light.png) |

### 项目管理 (Projects) (`/projects`)

- **交互统计**: 按钮 `5` 个，输入框 `1` 个，下拉框 `1` 个，Tab `0` 个
- **缺陷数**: `4` 处

**页面缺陷清单**:
- [LOW] **TEXT_CLIPPED**: 在 桌面 (1920x1080) 发现 2 处文本截断 (示例: "zhao_gu_deng_duan_dao_duan_zhe", "zhao_gu_deng_quan_liu_cheng_sh")
- [LOW] **TEXT_CLIPPED**: 在 笔记本 (1440x900) 发现 2 处文本截断 (示例: "zhao_gu_deng_duan_dao_duan_zhe", "zhao_gu_deng_quan_liu_cheng_sh")
- [LOW] **TEXT_CLIPPED**: 在 平板 (1024x768) 发现 2 处文本截断 (示例: "zhao_gu_deng_duan_dao_duan_zhe", "zhao_gu_deng_quan_liu_cheng_sh")
- [LOW] **TEXT_CLIPPED**: 在 移动端 (375x812) 发现 2 处文本截断 (示例: "zhao_gu_deng_duan_dao_duan_zhe", "zhao_gu_deng_quan_liu_cheng_sh")

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P02_projects\P02_projects_desktop_1080p_dark.png](screenshots\P02_projects\P02_projects_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P02_projects\P02_projects_laptop_900p_dark.png](screenshots\P02_projects\P02_projects_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P02_projects\P02_projects_tablet_768p_dark.png](screenshots\P02_projects\P02_projects_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P02_projects\P02_projects_mobile_375p_dark.png](screenshots\P02_projects\P02_projects_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P02_projects\P02_projects_desktop_1080p_light.png](screenshots\P02_projects\P02_projects_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P02_projects\P02_projects_mobile_375p_light.png](screenshots\P02_projects\P02_projects_mobile_375p_light.png) |

### 极速生成台 (Quick Create) (`/quick-create`)

- **交互统计**: 按钮 `7` 个，输入框 `4` 个，下拉框 `1` 个，Tab `0` 个
- **缺陷数**: `2` 处

**页面缺陷清单**:
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 72 处文本色彩对比度低于 WCAG AA 3.0:1 阈值
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 71 处文本色彩对比度低于 WCAG AA 3.0:1 阈值

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P03_quick_create\P03_quick_create_desktop_1080p_dark.png](screenshots\P03_quick_create\P03_quick_create_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P03_quick_create\P03_quick_create_laptop_900p_dark.png](screenshots\P03_quick_create\P03_quick_create_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P03_quick_create\P03_quick_create_tablet_768p_dark.png](screenshots\P03_quick_create\P03_quick_create_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P03_quick_create\P03_quick_create_mobile_375p_dark.png](screenshots\P03_quick_create\P03_quick_create_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P03_quick_create\P03_quick_create_desktop_1080p_light.png](screenshots\P03_quick_create\P03_quick_create_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P03_quick_create\P03_quick_create_mobile_375p_light.png](screenshots\P03_quick_create\P03_quick_create_mobile_375p_light.png) |

### 系统模型库 (Models) (`/system/capabilities`)

- **交互统计**: 按钮 `1` 个，输入框 `0` 个，下拉框 `1` 个，Tab `3` 个
- **缺陷数**: `2` 处

**页面缺陷清单**:
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 57 处文本色彩对比度低于 WCAG AA 3.0:1 阈值
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 56 处文本色彩对比度低于 WCAG AA 3.0:1 阈值

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P04_models\P04_models_desktop_1080p_dark.png](screenshots\P04_models\P04_models_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P04_models\P04_models_laptop_900p_dark.png](screenshots\P04_models\P04_models_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P04_models\P04_models_tablet_768p_dark.png](screenshots\P04_models\P04_models_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P04_models\P04_models_mobile_375p_dark.png](screenshots\P04_models\P04_models_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P04_models\P04_models_desktop_1080p_light.png](screenshots\P04_models\P04_models_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P04_models\P04_models_mobile_375p_light.png](screenshots\P04_models\P04_models_mobile_375p_light.png) |

### 系统任务队列 (Jobs) (`/system/jobs`)

- **交互统计**: 按钮 `1` 个，输入框 `0` 个，下拉框 `1` 个，Tab `0` 个
- **缺陷数**: `2` 处

**页面缺陷清单**:
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 15 处文本色彩对比度低于 WCAG AA 3.0:1 阈值
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 14 处文本色彩对比度低于 WCAG AA 3.0:1 阈值

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P05_jobs\P05_jobs_desktop_1080p_dark.png](screenshots\P05_jobs\P05_jobs_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P05_jobs\P05_jobs_laptop_900p_dark.png](screenshots\P05_jobs\P05_jobs_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P05_jobs\P05_jobs_tablet_768p_dark.png](screenshots\P05_jobs\P05_jobs_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P05_jobs\P05_jobs_mobile_375p_dark.png](screenshots\P05_jobs\P05_jobs_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P05_jobs\P05_jobs_desktop_1080p_light.png](screenshots\P05_jobs\P05_jobs_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P05_jobs\P05_jobs_mobile_375p_light.png](screenshots\P05_jobs\P05_jobs_mobile_375p_light.png) |

### 系统诊断面板 (Diagnostics) (`/system/diagnostics`)

- **交互统计**: 按钮 `1` 个，输入框 `1` 个，下拉框 `1` 个，Tab `3` 个
- **缺陷数**: `2` 处

**页面缺陷清单**:
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 12 处文本色彩对比度低于 WCAG AA 3.0:1 阈值
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 11 处文本色彩对比度低于 WCAG AA 3.0:1 阈值

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P06_diagnostics\P06_diagnostics_desktop_1080p_dark.png](screenshots\P06_diagnostics\P06_diagnostics_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P06_diagnostics\P06_diagnostics_laptop_900p_dark.png](screenshots\P06_diagnostics\P06_diagnostics_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P06_diagnostics\P06_diagnostics_tablet_768p_dark.png](screenshots\P06_diagnostics\P06_diagnostics_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P06_diagnostics\P06_diagnostics_mobile_375p_dark.png](screenshots\P06_diagnostics\P06_diagnostics_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P06_diagnostics\P06_diagnostics_desktop_1080p_light.png](screenshots\P06_diagnostics\P06_diagnostics_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P06_diagnostics\P06_diagnostics_mobile_375p_light.png](screenshots\P06_diagnostics\P06_diagnostics_mobile_375p_light.png) |

### 系统工作流 (Workflows) (`/system/workflows`)

- **交互统计**: 按钮 `1` 个，输入框 `14` 个，下拉框 `1` 个，Tab `0` 个
- **缺陷数**: `2` 处

**页面缺陷清单**:
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 54 处文本色彩对比度低于 WCAG AA 3.0:1 阈值
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 53 处文本色彩对比度低于 WCAG AA 3.0:1 阈值

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P07_workflows\P07_workflows_desktop_1080p_dark.png](screenshots\P07_workflows\P07_workflows_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P07_workflows\P07_workflows_laptop_900p_dark.png](screenshots\P07_workflows\P07_workflows_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P07_workflows\P07_workflows_tablet_768p_dark.png](screenshots\P07_workflows\P07_workflows_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P07_workflows\P07_workflows_mobile_375p_dark.png](screenshots\P07_workflows\P07_workflows_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P07_workflows\P07_workflows_desktop_1080p_light.png](screenshots\P07_workflows\P07_workflows_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P07_workflows\P07_workflows_mobile_375p_light.png](screenshots\P07_workflows\P07_workflows_mobile_375p_light.png) |

### 项目总览看板 (Project Home) (`/projects/7e60549f-5253-49b0-b055-9f5ee64bfa24`)

- **交互统计**: 按钮 `5` 个，输入框 `0` 个，下拉框 `1` 个，Tab `0` 个
- **缺陷数**: `2` 处

**页面缺陷清单**:
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 26 处文本色彩对比度低于 WCAG AA 3.0:1 阈值
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 24 处文本色彩对比度低于 WCAG AA 3.0:1 阈值

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P08_project_home\P08_project_home_desktop_1080p_dark.png](screenshots\P08_project_home\P08_project_home_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P08_project_home\P08_project_home_laptop_900p_dark.png](screenshots\P08_project_home\P08_project_home_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P08_project_home\P08_project_home_tablet_768p_dark.png](screenshots\P08_project_home\P08_project_home_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P08_project_home\P08_project_home_mobile_375p_dark.png](screenshots\P08_project_home\P08_project_home_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P08_project_home\P08_project_home_desktop_1080p_light.png](screenshots\P08_project_home\P08_project_home_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P08_project_home\P08_project_home_mobile_375p_light.png](screenshots\P08_project_home\P08_project_home_mobile_375p_light.png) |

### 故事工作区 (Story) (`/projects/7e60549f-5253-49b0-b055-9f5ee64bfa24/story`)

- **交互统计**: 按钮 `7` 个，输入框 `1` 个，下拉框 `1` 个，Tab `3` 个
- **缺陷数**: `2` 处

**页面缺陷清单**:
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 21 处文本色彩对比度低于 WCAG AA 3.0:1 阈值
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 19 处文本色彩对比度低于 WCAG AA 3.0:1 阈值

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P09_story\P09_story_desktop_1080p_dark.png](screenshots\P09_story\P09_story_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P09_story\P09_story_laptop_900p_dark.png](screenshots\P09_story\P09_story_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P09_story\P09_story_tablet_768p_dark.png](screenshots\P09_story\P09_story_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P09_story\P09_story_mobile_375p_dark.png](screenshots\P09_story\P09_story_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P09_story\P09_story_desktop_1080p_light.png](screenshots\P09_story\P09_story_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P09_story\P09_story_mobile_375p_light.png](screenshots\P09_story\P09_story_mobile_375p_light.png) |

### 改编策划列表 (Story Plans) (`/projects/5bf496fd-6f60-4f24-8a6a-e54b0be0f0ec/story/plans`)

- **交互统计**: 按钮 `5` 个，输入框 `6` 个，下拉框 `1` 个，Tab `0` 个
- **缺陷数**: `2` 处

**页面缺陷清单**:
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 24 处文本色彩对比度低于 WCAG AA 3.0:1 阈值
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 22 处文本色彩对比度低于 WCAG AA 3.0:1 阈值

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P10_story_plans\P10_story_plans_desktop_1080p_dark.png](screenshots\P10_story_plans\P10_story_plans_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P10_story_plans\P10_story_plans_laptop_900p_dark.png](screenshots\P10_story_plans\P10_story_plans_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P10_story_plans\P10_story_plans_tablet_768p_dark.png](screenshots\P10_story_plans\P10_story_plans_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P10_story_plans\P10_story_plans_mobile_375p_dark.png](screenshots\P10_story_plans\P10_story_plans_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P10_story_plans\P10_story_plans_desktop_1080p_light.png](screenshots\P10_story_plans\P10_story_plans_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P10_story_plans\P10_story_plans_mobile_375p_light.png](screenshots\P10_story_plans\P10_story_plans_mobile_375p_light.png) |

### 改编策划详情 (Plan Workspace) (`/projects/5bf496fd-6f60-4f24-8a6a-e54b0be0f0ec/story/plans/7b7039ef-ceb3-4f3e-b963-c97005081782`)

- **交互统计**: 按钮 `5` 个，输入框 `0` 个，下拉框 `1` 个，Tab `0` 个
- **缺陷数**: `1` 处

**页面缺陷清单**:
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 12 处文本色彩对比度低于 WCAG AA 3.0:1 阈值

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P11_plan_workspace\P11_plan_workspace_desktop_1080p_dark.png](screenshots\P11_plan_workspace\P11_plan_workspace_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P11_plan_workspace\P11_plan_workspace_laptop_900p_dark.png](screenshots\P11_plan_workspace\P11_plan_workspace_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P11_plan_workspace\P11_plan_workspace_tablet_768p_dark.png](screenshots\P11_plan_workspace\P11_plan_workspace_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P11_plan_workspace\P11_plan_workspace_mobile_375p_dark.png](screenshots\P11_plan_workspace\P11_plan_workspace_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P11_plan_workspace\P11_plan_workspace_desktop_1080p_light.png](screenshots\P11_plan_workspace\P11_plan_workspace_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P11_plan_workspace\P11_plan_workspace_mobile_375p_light.png](screenshots\P11_plan_workspace\P11_plan_workspace_mobile_375p_light.png) |

### 资产圣经 (Asset Bible) (`/projects/7e60549f-5253-49b0-b055-9f5ee64bfa24/assets`)

- **交互统计**: 按钮 `5` 个，输入框 `0` 个，下拉框 `1` 个，Tab `3` 个
- **缺陷数**: `2` 处

**页面缺陷清单**:
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 15 处文本色彩对比度低于 WCAG AA 3.0:1 阈值
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 13 处文本色彩对比度低于 WCAG AA 3.0:1 阈值

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P12_asset_bible\P12_asset_bible_desktop_1080p_dark.png](screenshots\P12_asset_bible\P12_asset_bible_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P12_asset_bible\P12_asset_bible_laptop_900p_dark.png](screenshots\P12_asset_bible\P12_asset_bible_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P12_asset_bible\P12_asset_bible_tablet_768p_dark.png](screenshots\P12_asset_bible\P12_asset_bible_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P12_asset_bible\P12_asset_bible_mobile_375p_dark.png](screenshots\P12_asset_bible\P12_asset_bible_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P12_asset_bible\P12_asset_bible_desktop_1080p_light.png](screenshots\P12_asset_bible\P12_asset_bible_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P12_asset_bible\P12_asset_bible_mobile_375p_light.png](screenshots\P12_asset_bible\P12_asset_bible_mobile_375p_light.png) |

### 生产设置 (Settings Production) (`/projects/7e60549f-5253-49b0-b055-9f5ee64bfa24/settings/production`)

- **交互统计**: 按钮 `1` 个，输入框 `0` 个，下拉框 `1` 个，Tab `0` 个
- **缺陷数**: `2` 处

**页面缺陷清单**:
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 26 处文本色彩对比度低于 WCAG AA 3.0:1 阈值
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 24 处文本色彩对比度低于 WCAG AA 3.0:1 阈值

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P13_settings_production\P13_settings_production_desktop_1080p_dark.png](screenshots\P13_settings_production\P13_settings_production_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P13_settings_production\P13_settings_production_laptop_900p_dark.png](screenshots\P13_settings_production\P13_settings_production_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P13_settings_production\P13_settings_production_tablet_768p_dark.png](screenshots\P13_settings_production\P13_settings_production_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P13_settings_production\P13_settings_production_mobile_375p_dark.png](screenshots\P13_settings_production\P13_settings_production_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P13_settings_production\P13_settings_production_desktop_1080p_light.png](screenshots\P13_settings_production\P13_settings_production_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P13_settings_production\P13_settings_production_mobile_375p_light.png](screenshots\P13_settings_production\P13_settings_production_mobile_375p_light.png) |

### 导演配方设置 (Settings Directing) (`/projects/7e60549f-5253-49b0-b055-9f5ee64bfa24/settings/directing`)

- **交互统计**: 按钮 `1` 个，输入框 `11` 个，下拉框 `1` 个，Tab `0` 个
- **缺陷数**: `2` 处

**页面缺陷清单**:
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 26 处文本色彩对比度低于 WCAG AA 3.0:1 阈值
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 24 处文本色彩对比度低于 WCAG AA 3.0:1 阈值

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P14_settings_directing\P14_settings_directing_desktop_1080p_dark.png](screenshots\P14_settings_directing\P14_settings_directing_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P14_settings_directing\P14_settings_directing_laptop_900p_dark.png](screenshots\P14_settings_directing\P14_settings_directing_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P14_settings_directing\P14_settings_directing_tablet_768p_dark.png](screenshots\P14_settings_directing\P14_settings_directing_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P14_settings_directing\P14_settings_directing_mobile_375p_dark.png](screenshots\P14_settings_directing\P14_settings_directing_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P14_settings_directing\P14_settings_directing_desktop_1080p_light.png](screenshots\P14_settings_directing\P14_settings_directing_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P14_settings_directing\P14_settings_directing_mobile_375p_light.png](screenshots\P14_settings_directing\P14_settings_directing_mobile_375p_light.png) |

### 质检策略设置 (Settings Quality) (`/projects/7e60549f-5253-49b0-b055-9f5ee64bfa24/settings/quality`)

- **交互统计**: 按钮 `1` 个，输入框 `26` 个，下拉框 `1` 个，Tab `1` 个
- **缺陷数**: `2` 处

**页面缺陷清单**:
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 55 处文本色彩对比度低于 WCAG AA 3.0:1 阈值
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 53 处文本色彩对比度低于 WCAG AA 3.0:1 阈值

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P15_settings_quality\P15_settings_quality_desktop_1080p_dark.png](screenshots\P15_settings_quality\P15_settings_quality_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P15_settings_quality\P15_settings_quality_laptop_900p_dark.png](screenshots\P15_settings_quality\P15_settings_quality_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P15_settings_quality\P15_settings_quality_tablet_768p_dark.png](screenshots\P15_settings_quality\P15_settings_quality_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P15_settings_quality\P15_settings_quality_mobile_375p_dark.png](screenshots\P15_settings_quality\P15_settings_quality_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P15_settings_quality\P15_settings_quality_desktop_1080p_light.png](screenshots\P15_settings_quality\P15_settings_quality_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P15_settings_quality\P15_settings_quality_mobile_375p_light.png](screenshots\P15_settings_quality\P15_settings_quality_mobile_375p_light.png) |

### 视觉实验室列表 (Visual Labs) (`/projects/5bf496fd-6f60-4f24-8a6a-e54b0be0f0ec/labs`)

- **交互统计**: 按钮 `5` 个，输入框 `0` 个，下拉框 `1` 个，Tab `0` 个
- **缺陷数**: `1` 处

**页面缺陷清单**:
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 11 处文本色彩对比度低于 WCAG AA 3.0:1 阈值

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P16_labs\P16_labs_desktop_1080p_dark.png](screenshots\P16_labs\P16_labs_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P16_labs\P16_labs_laptop_900p_dark.png](screenshots\P16_labs\P16_labs_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P16_labs\P16_labs_tablet_768p_dark.png](screenshots\P16_labs\P16_labs_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P16_labs\P16_labs_mobile_375p_dark.png](screenshots\P16_labs\P16_labs_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P16_labs\P16_labs_desktop_1080p_light.png](screenshots\P16_labs\P16_labs_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P16_labs\P16_labs_mobile_375p_light.png](screenshots\P16_labs\P16_labs_mobile_375p_light.png) |

### 视觉实验室工作台 (Visual Lab Detail) (`/projects/5bf496fd-6f60-4f24-8a6a-e54b0be0f0ec/labs/3726019a-bda8-4aed-a425-4c49a9d5ab8f`)

- **交互统计**: 按钮 `5` 个，输入框 `0` 个，下拉框 `1` 个，Tab `0` 个
- **缺陷数**: `0` 处

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P17_lab_workspace\P17_lab_workspace_desktop_1080p_dark.png](screenshots\P17_lab_workspace\P17_lab_workspace_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P17_lab_workspace\P17_lab_workspace_laptop_900p_dark.png](screenshots\P17_lab_workspace\P17_lab_workspace_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P17_lab_workspace\P17_lab_workspace_tablet_768p_dark.png](screenshots\P17_lab_workspace\P17_lab_workspace_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P17_lab_workspace\P17_lab_workspace_mobile_375p_dark.png](screenshots\P17_lab_workspace\P17_lab_workspace_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P17_lab_workspace\P17_lab_workspace_desktop_1080p_light.png](screenshots\P17_lab_workspace\P17_lab_workspace_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P17_lab_workspace\P17_lab_workspace_mobile_375p_light.png](screenshots\P17_lab_workspace\P17_lab_workspace_mobile_375p_light.png) |

### 分集策划台 (Episode Plan) (`/projects/7e60549f-5253-49b0-b055-9f5ee64bfa24/episodes/8c9c0efa-8c16-4d46-ae53-db206c5ee6a5/plan`)

- **交互统计**: 按钮 `5` 个，输入框 `0` 个，下拉框 `1` 个，Tab `0` 个
- **缺陷数**: `2` 处

**页面缺陷清单**:
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 20 处文本色彩对比度低于 WCAG AA 3.0:1 阈值
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 19 处文本色彩对比度低于 WCAG AA 3.0:1 阈值

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P18_episode_plan\P18_episode_plan_desktop_1080p_dark.png](screenshots\P18_episode_plan\P18_episode_plan_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P18_episode_plan\P18_episode_plan_laptop_900p_dark.png](screenshots\P18_episode_plan\P18_episode_plan_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P18_episode_plan\P18_episode_plan_tablet_768p_dark.png](screenshots\P18_episode_plan\P18_episode_plan_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P18_episode_plan\P18_episode_plan_mobile_375p_dark.png](screenshots\P18_episode_plan\P18_episode_plan_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P18_episode_plan\P18_episode_plan_desktop_1080p_light.png](screenshots\P18_episode_plan\P18_episode_plan_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P18_episode_plan\P18_episode_plan_mobile_375p_light.png](screenshots\P18_episode_plan\P18_episode_plan_mobile_375p_light.png) |

### 导演镜头工作台 (Director Desk) (`/projects/7e60549f-5253-49b0-b055-9f5ee64bfa24/episodes/8c9c0efa-8c16-4d46-ae53-db206c5ee6a5/studio`)

- **交互统计**: 按钮 `5` 个，输入框 `0` 个，下拉框 `1` 个，Tab `0` 个
- **缺陷数**: `2` 处

**页面缺陷清单**:
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 13 处文本色彩对比度低于 WCAG AA 3.0:1 阈值
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 12 处文本色彩对比度低于 WCAG AA 3.0:1 阈值

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P19_director_desk\P19_director_desk_desktop_1080p_dark.png](screenshots\P19_director_desk\P19_director_desk_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P19_director_desk\P19_director_desk_laptop_900p_dark.png](screenshots\P19_director_desk\P19_director_desk_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P19_director_desk\P19_director_desk_tablet_768p_dark.png](screenshots\P19_director_desk\P19_director_desk_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P19_director_desk\P19_director_desk_mobile_375p_dark.png](screenshots\P19_director_desk\P19_director_desk_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P19_director_desk\P19_director_desk_desktop_1080p_light.png](screenshots\P19_director_desk\P19_director_desk_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P19_director_desk\P19_director_desk_mobile_375p_light.png](screenshots\P19_director_desk\P19_director_desk_mobile_375p_light.png) |

### 镜头精修视图 (Shot Detail) (`/projects/7e60549f-5253-49b0-b055-9f5ee64bfa24/episodes/8c9c0efa-8c16-4d46-ae53-db206c5ee6a5/studio/cd0a399d-0cee-4d0c-98cf-d161262d7be6`)

- **交互统计**: 按钮 `5` 个，输入框 `1` 个，下拉框 `1` 个，Tab `2` 个
- **缺陷数**: `3` 处

**页面缺陷清单**:
- [LOW] **TEXT_CLIPPED**: 在 移动端 (375x812) 发现 1 处文本截断 (示例: "镜头 cd0a399d-0cee-4d0c-98cf-d16")
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 24 处文本色彩对比度低于 WCAG AA 3.0:1 阈值
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 13 处文本色彩对比度低于 WCAG AA 3.0:1 阈值

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P20_shot_detail\P20_shot_detail_desktop_1080p_dark.png](screenshots\P20_shot_detail\P20_shot_detail_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P20_shot_detail\P20_shot_detail_laptop_900p_dark.png](screenshots\P20_shot_detail\P20_shot_detail_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P20_shot_detail\P20_shot_detail_tablet_768p_dark.png](screenshots\P20_shot_detail\P20_shot_detail_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P20_shot_detail\P20_shot_detail_mobile_375p_dark.png](screenshots\P20_shot_detail\P20_shot_detail_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P20_shot_detail\P20_shot_detail_desktop_1080p_light.png](screenshots\P20_shot_detail\P20_shot_detail_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P20_shot_detail\P20_shot_detail_mobile_375p_light.png](screenshots\P20_shot_detail\P20_shot_detail_mobile_375p_light.png) |

### 后期审核中心 (Post Review) (`/projects/7e60549f-5253-49b0-b055-9f5ee64bfa24/episodes/8c9c0efa-8c16-4d46-ae53-db206c5ee6a5/post/review`)

- **交互统计**: 按钮 `5` 个，输入框 `1` 个，下拉框 `1` 个，Tab `0` 个
- **缺陷数**: `2` 处

**页面缺陷清单**:
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 15 处文本色彩对比度低于 WCAG AA 3.0:1 阈值
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 14 处文本色彩对比度低于 WCAG AA 3.0:1 阈值

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P21_post_review\P21_post_review_desktop_1080p_dark.png](screenshots\P21_post_review\P21_post_review_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P21_post_review\P21_post_review_laptop_900p_dark.png](screenshots\P21_post_review\P21_post_review_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P21_post_review\P21_post_review_tablet_768p_dark.png](screenshots\P21_post_review\P21_post_review_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P21_post_review\P21_post_review_mobile_375p_dark.png](screenshots\P21_post_review\P21_post_review_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P21_post_review\P21_post_review_desktop_1080p_light.png](screenshots\P21_post_review\P21_post_review_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P21_post_review\P21_post_review_mobile_375p_light.png](screenshots\P21_post_review\P21_post_review_mobile_375p_light.png) |

### 声音工作区 (Post Audio) (`/projects/7e60549f-5253-49b0-b055-9f5ee64bfa24/episodes/8c9c0efa-8c16-4d46-ae53-db206c5ee6a5/post/audio`)

- **交互统计**: 按钮 `1` 个，输入框 `0` 个，下拉框 `1` 个，Tab `0` 个
- **缺陷数**: `2` 处

**页面缺陷清单**:
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 14 处文本色彩对比度低于 WCAG AA 3.0:1 阈值
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 13 处文本色彩对比度低于 WCAG AA 3.0:1 阈值

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P22_post_audio\P22_post_audio_desktop_1080p_dark.png](screenshots\P22_post_audio\P22_post_audio_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P22_post_audio\P22_post_audio_laptop_900p_dark.png](screenshots\P22_post_audio\P22_post_audio_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P22_post_audio\P22_post_audio_tablet_768p_dark.png](screenshots\P22_post_audio\P22_post_audio_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P22_post_audio\P22_post_audio_mobile_375p_dark.png](screenshots\P22_post_audio\P22_post_audio_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P22_post_audio\P22_post_audio_desktop_1080p_light.png](screenshots\P22_post_audio\P22_post_audio_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P22_post_audio\P22_post_audio_mobile_375p_light.png](screenshots\P22_post_audio\P22_post_audio_mobile_375p_light.png) |

### 时间线剪辑台 (Timeline) (`/projects/7e60549f-5253-49b0-b055-9f5ee64bfa24/episodes/8c9c0efa-8c16-4d46-ae53-db206c5ee6a5/post/edit`)

- **交互统计**: 按钮 `1` 个，输入框 `6` 个，下拉框 `1` 个，Tab `0` 个
- **缺陷数**: `4` 处

**页面缺陷清单**:
- [LOW] **TEXT_CLIPPED**: 在 笔记本 (1440x900) 发现 1 处文本截断 (示例: "PROFILE_I2V_EVIDENCE_001")
- [LOW] **TEXT_CLIPPED**: 在 平板 (1024x768) 发现 1 处文本截断 (示例: "PROFILE_I2V_EVIDENCE_001")
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 25 处文本色彩对比度低于 WCAG AA 3.0:1 阈值
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 24 处文本色彩对比度低于 WCAG AA 3.0:1 阈值

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P23_post_edit\P23_post_edit_desktop_1080p_dark.png](screenshots\P23_post_edit\P23_post_edit_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P23_post_edit\P23_post_edit_laptop_900p_dark.png](screenshots\P23_post_edit\P23_post_edit_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P23_post_edit\P23_post_edit_tablet_768p_dark.png](screenshots\P23_post_edit\P23_post_edit_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P23_post_edit\P23_post_edit_mobile_375p_dark.png](screenshots\P23_post_edit\P23_post_edit_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P23_post_edit\P23_post_edit_desktop_1080p_light.png](screenshots\P23_post_edit\P23_post_edit_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P23_post_edit\P23_post_edit_mobile_375p_light.png](screenshots\P23_post_edit\P23_post_edit_mobile_375p_light.png) |

### 成片交付中心 (Delivery) (`/projects/7e60549f-5253-49b0-b055-9f5ee64bfa24/episodes/8c9c0efa-8c16-4d46-ae53-db206c5ee6a5/delivery`)

- **交互统计**: 按钮 `5` 个，输入框 `0` 个，下拉框 `1` 个，Tab `0` 个
- **缺陷数**: `2` 处

**页面缺陷清单**:
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 15 处文本色彩对比度低于 WCAG AA 3.0:1 阈值
- [MEDIUM] **LOW_CONTRAST_RATIO**: 在浅色模式下检测到 14 处文本色彩对比度低于 WCAG AA 3.0:1 阈值

**各分辨率与主题视觉快照**:

| 分辨率/主题 | 截图预览文件相对路径 |
| :--- | :--- |
| `desktop_1080p` (dark) | [screenshots\P24_delivery\P24_delivery_desktop_1080p_dark.png](screenshots\P24_delivery\P24_delivery_desktop_1080p_dark.png) |
| `laptop_900p` (dark) | [screenshots\P24_delivery\P24_delivery_laptop_900p_dark.png](screenshots\P24_delivery\P24_delivery_laptop_900p_dark.png) |
| `tablet_768p` (dark) | [screenshots\P24_delivery\P24_delivery_tablet_768p_dark.png](screenshots\P24_delivery\P24_delivery_tablet_768p_dark.png) |
| `mobile_375p` (dark) | [screenshots\P24_delivery\P24_delivery_mobile_375p_dark.png](screenshots\P24_delivery\P24_delivery_mobile_375p_dark.png) |
| `desktop_1080p` (light_sim) | [screenshots\P24_delivery\P24_delivery_desktop_1080p_light.png](screenshots\P24_delivery\P24_delivery_desktop_1080p_light.png) |
| `mobile_375p` (light_sim) | [screenshots\P24_delivery\P24_delivery_mobile_375p_light.png](screenshots\P24_delivery\P24_delivery_mobile_375p_light.png) |

---

## 4. 前端架构与 UI/UX 优化改进建议 (Recommendations)

1. **移动端与平板响应式排版 (Responsive Adaptability)**:
   - 部分制作台工作区（如导演镜头工作台、时间线剪辑台）设计初衷为桌面宽屏工作站（min-width ≥ 1280px）。在 1024px 与 375px 下会出现横向滚动条。
   - **优化方案**: 在根容器引入 CSS 媒体查询断点（如 `@media (max-width: 1024px)`），对侧边栏与多栏布局启用折叠抽屉（Drawer）或横向流式切换。

2. **主题色令牌统一性 (Semantic Design Tokens)**:
   - 系统默认的暗色工控风格非常统一，但有少量组件硬编码了白底或透明背景配合白色文字。
   - **优化方案**: 严格杜绝在组件局部写入 `color: #fff`，全部改用 `var(--text)`、`var(--text-muted)`，确保未来扩展浅色或高对比度模式时能一键无缝自适应。

3. **触控热区与微交互 (Touch Targets & Accessibility)**:
   - 少量图标按钮尺寸仅为 24x24px，在移动视口或触屏设备上容易误触。
   - **优化方案**: 为小尺寸图标按钮增加伪元素扩充点击热区（如 `::after` 设置 `min-width: 36px; min-height: 36px`）。

