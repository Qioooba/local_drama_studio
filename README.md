# LocalDramaStudio

LocalDramaStudio 是一个本地优先的 AI 短剧 / 漫剧生产工作台：从小说或剧本文档出发，完成故事规划、资产管理、分集拆解、镜头设计、图像/视频生成、配音、剪辑、审核与交付。

当前仓库对应 `0.1.0` development channel。它是源码与发行工程仓库，不包含任何模型权重、用户音色、BGM、图片或视频素材；这些运行时资源必须由使用者在本机准备并通过能力校验后使用。

## 适合做什么

- 把 TXT、Markdown、DOCX、PDF、EPUB 或粘贴的长文本整理为可生产的故事、分集、场景、人物和镜头草稿。
- 在同一套项目数据中维护角色、场景、道具、服装、身份包、参考图和跨镜头连续性。
- 通过 Shot Studio 设计镜头意图、运镜、提示词、首尾帧、动作控制和候选版本，并明确区分“生成”“采用”“批准”。
- 使用本机 ComfyUI/H3、托管 `llama.cpp`、Windows SAPI、VoxCPM2、LatentSync 与 FFmpeg 完成受控的媒体生产链。
- 将采用的镜头、对白、BGM/SFX、字幕和转场编排为不可变时间线，渲染并生成经过文件校验的交付包。
- 用持续化 Job、Worker、GPU 资源租约、审计事件、版本指纹和恢复集管理长时间运行的本地生产任务。

## 从原稿到交付

```text
原稿 / 粘贴正文
        ↓
故事规划 → 分集 / 场景 / 资产建议 → 人工确认并应用
        ↓
分集策划 → Shot Studio → 关键帧 / 视频候选 → 人工采用与审核
        ↓
对白与 TTS → BGM / SFX → 字幕与初剪 → 冻结 TimelineRevision
        ↓
整集渲染 → 交付预检 → 人工批准 → 验证与打包
```

主创作路由由前端 `AppShell` 统一管理：

| 工作区 | 入口 | 主要职责 |
|---|---|---|
| 项目库 | `/projects` | 创建、复制、归档项目；管理季度、分集与项目包 |
| 快速生成 | `/quick-create` | 用一句话完成本地文生图 / 图生视频试运行 |
| 故事 | `/projects/:projectId/story` | 上传或选择原稿，运行 AI 全剧规划并审查草稿 |
| 资产 | `/projects/:projectId/assets` | 角色、场景、道具、服装、参考图与身份包 |
| 改编规划 | `/projects/:projectId/story/plans` | 读取来源、生成分析、审批并物化改编计划 |
| 分集策划 | `/projects/:projectId/episodes/:episodeId/plan` | 分镜表、场景范围、镜头组、生产准备与异常处理 |
| 镜头 | `/projects/:projectId/episodes/:episodeId/studio` | 导演意图、连续性、生成、候选比较、对白与口型同步 |
| 生产 | `/projects/:projectId/episodes/:episodeId/production` | 整集运行、任务状态、暂停/恢复、重试与异常恢复 |
| 后期 / 审核 | `/projects/:projectId/episodes/:episodeId/post/review` | 视频审核、人工决策、逐帧批注与采用事实 |
| 后期 / 声音 | `/projects/:projectId/episodes/:episodeId/post/audio` | 对白、TTS、音频缺口、BGM/SFX 与混音 |
| 后期 / 编辑 | `/projects/:projectId/episodes/:episodeId/post/edit` | TimelineRevision、镜头顺序、时长、转场与字幕 |
| 交付 | `/projects/:projectId/episodes/:episodeId/delivery` | 渲染、文件验证、平台规格、人工批准与交付包 |
| Visual Lab | `/projects/:projectId/labs` | 面向高级用户的实验画布、预检、运行与正式采纳 |
| 系统中心 | `/system/*` | 能力 / 模型、任务、诊断、工作流与本机运行状态 |

## 已实现能力

### 故事、改编与项目

- 项目、Season、Episode、Scene、Shot 的结构化管理，支持目标时长和分集目录。
- 原稿导入、段落分页、来源片段引用、文本哈希和本地知识检索。
- 本地 LLM 驱动的故事规划、角色 / 场景 / 道具提取、剧本拆解和分镜草稿。
- AI 结果先进入可审查的 draft；确认后才写入正式故事事实，支持幂等应用、局部修订和人工审批。
- 改编计划的预检、分析运行、审批、物化，以及项目级知识索引和重试。
- 项目包导出、上传、暂存、dry-run、重新绑定或作为副本导入。

### 资产 Bible 与连续性

- 四类故事资产：`CHARACTER`、`SCENE`、`PROP`、`COSTUME`。
- 资产状态、参考版本、canonical reference、镜头绑定和跨项目授权（`READ_ONLY` / `DERIVED`）。
- 角色 Identity Pack：正面 / 左右侧 / 背面等槽位，版本化、审批、影响分析、过期传播与不可变快照。
- 角色近景、表情九宫格、多视图和场景参考生成；角色锚点会在白名单语义输入中注入镜头生成提示词。
- 本地角色声线绑定、2–15 秒已验证参考音频的 VoxCPM2 零样本克隆，并要求用户确认声音授权。

### 镜头与生成

- Shot Studio 统一承载导演意图、镜头 revision、连续性上下文、资产 / 身份包、关键帧、视频和对白。
- 支持本机能力校验后的 T2V、I2V、首尾帧 FL2VA、受控运动蒙版 / 向量 / 关键帧、Ref2V 能力位和生成档位。
- 支持固定 seed、生成 Variant、候选比较、重抽、精确重放（EXACT_REPLAY）和来源 / Profile / Workflow / 模型指纹冻结。
- 首帧、尾帧、中间锚点以及前后镜头 frame bridge 均按 MediaVersion 和 FFprobe 时间戳登记。
- 支持单句生成视频、图像候选选择后再进入图生视频，以及镜头级 LatentSync 口型同步。
- H3 原生工作流通过 ComfyUI 节点、模型清单、Profile、Workflow Version、Runtime Environment 和真实 smoke 逐级门禁。

### 声音、后期与交付

- 对白文本 revision、角色音色绑定、情绪 / 语速、逐行 TTS 候选、A/B 试听、选择与批量整集 TTS。
- Windows SAPI 和本机 VoxCPM2 生产 TTS；Qwen3 ASR / ForcedAligner 可用于逐字时间轴切分。
- 音频轨道支持 `DIALOGUE`、`BGM`、`SFX`，混音参数包括音量、淡入淡出、延迟和循环。
- TimelineRevision、字幕 revision、ASS / SRT / VTT、字幕样式模板、转场约束、视频增强配方和分段合成。
- FFmpeg / FFprobe 整集渲染、联系表导出、交付预设（如竖屏短视频、横屏与 3:4）、交付文件清单和 SHA-256 校验。
- 支持 OTIO、EDL 和社区逆向 best-effort 的剪映 `draft_content.json` 导出；剪映兼容性仍应按目标版本实机验证。
- ReviewDecision、逐帧批注、整集审核、交付验证和人工批准彼此独立，机器检查通过不等于人工批准。

### 任务、模型与运维

- SQLite WAL 保存项目事实、Job / Attempt、版本、选择、审核、审计和 outbox；项目文件系统保存媒体，cache 可重建。
- 持久化 Worker 会话、心跳、租约、重试、取消、批量操作、崩溃恢复和 CPU / GPU 通道隔离。
- GPU runtime coordinator 负责 ComfyUI/H3 与托管 `llama-server` 的单卡切换、驱逐、空闲显存门禁和进程回收。
- Model Center 从 `config/model-lock.json` 与受控 Model Library roots 发现 ComfyUI / PyTorch 模型，并经过完整性、能力 smoke、Profile 与 Workflow 发布后才可分配。
- 默认文本运行时为 `LLAMA_CPP_MANAGED`；Ollama 相关代码仅保留兼容导入 / 发现能力，不代表生产默认 Provider。
- System Center 提供本机能力、Jobs、容量观测、诊断、审计、跨项目搜索、工作流与 Comfy Lab。
- 原生 Go Runtime Host 统一负责配置迁移、数据库维护、API / Worker 生命周期、健康检查、升级、回滚、服务和防火墙协同。

## 架构概览

```text
浏览器
  │
  ▼
React 19 + TypeScript + React Router + TanStack Query + Vite
  │  /api/v1、/api/v2；契约版本启动校验；SSE/outbox 事件
  ▼
FastAPI API（apps/api/local_drama）
  ├─ domain：能力、网络、媒体、版本与业务不变量
  ├─ application：查询、命令、编排、时间线、审核与 Worker handlers
  ├─ infrastructure：SQLite/WAL、文件系统、Comfy、LLM、GPU 与平台适配器
  └─ model_platform：ModelRoot、发现、完整性、Profile、Workflow、Runtime
       │
       ├─ SQLite/WAL：事实、状态、版本、任务、审计
       ├─ projects/work/cache/logs/backups：受控可变目录
       └─ durable Worker：ComfyUI/H3、llama.cpp、TTS、LatentSync、FFmpeg

Go Runtime Host（cmd/runtime-host）
  └─ 维护 → 启动 API + Worker → 健康检查 → 停止 / 升级 / 回滚
```

代码边界保持模块化单体形态：API 与 Worker 可分进程部署，但共享同一套 schema、项目目录、Job 合同和版本协议；不会通过隐式调用脚本或动态生成配置绕过业务门禁。

## 安全与数据边界

- 默认 `LOCAL_ONLY`：只允许 loopback、本地进程和受控本地 executable，禁止公网 Provider、云模型、云音色、计费和多租户。
- `LAN_SERVICE` 必须显式设置 `trusted_lan_unauthenticated=true`；当前没有应用登录鉴权，只适合可信局域网，并应使用防火墙限制端口。
- 远程浏览器只通过上传 / 下载 API 传递文件，不提交客户端路径、不打开服务器文件选择框，也不会读取服务器任意路径。
- 模型、媒体、音色和授权证据不随仓库或发行包分发；模型只保存受控库中的引用、相对路径、完整性信息和哈希。
- 关键写操作使用 revision / plan hash / idempotency key 做并发保护；重试创建新 Attempt，创作重抽创建新 Variant，不覆盖旧版本。
- 生产状态以 SQLite/WAL 为权威，媒体以项目文件系统为权威；审计和版本记录不可覆盖，存储失败进入可见 quarantine。

网络模式：

| `LOCAL_DRAMA_NETWORK_MODE` | 行为 |
|---|---|
| `LOCAL_ONLY`（默认） | API 绑定 loopback，适合桌面和开发；只访问本机运行时。 |
| `LAN_SERVICE` | API 可绑定局域网地址并直接托管 `apps/web/dist`，浏览器通过单端口访问；仍禁止公网地址。 |

## 开发环境

### 前置依赖

- Python `3.12.x`（`apps/api/pyproject.toml` 要求 `>=3.12,<3.13`）。
- Node.js 与 pnpm `9.15.9`，版本由根目录 `package.json` 声明。
- Go：仅在使用 Runtime Host 开发入口或构建发行包时需要。
- 具体能力还需要本机准备 FFmpeg / FFprobe、ComfyUI/H3、模型文件、llama.cpp、TTS 或 LatentSync；仓库不会自动下载或捆绑它们。

### 安装

在仓库根目录执行：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r apps\api\requirements.lock -r apps\api\requirements-runtime.lock
pnpm install
$env:PYTHONPATH = (Join-Path (Get-Location) 'apps/api')
.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade heads
```

当前迁移预期 head 由 [`docs/release/migration-contract.json`](docs/release/migration-contract.json) 约束；不要根据旧报告中的迁移编号手工回退数据库。

### 启动开发服务

推荐打开两个 PowerShell 窗口：

```powershell
# 窗口 1：API + 内嵌开发 Worker，默认 http://127.0.0.1:3210
.\scripts\dev\start_api.ps1

# 窗口 2：Vite，默认 http://127.0.0.1:5173；/api 代理到 3210
.\scripts\dev\start_web.ps1
```

浏览器打开 <http://127.0.0.1:5173>。Vite 的 API 代理可用 `LOCAL_DRAMA_API_PROXY` 指向其他本地 API 地址。

需要让 Runtime Host 管理 API、独立 Worker 和维护流程时，可使用：

```powershell
.\scripts\start.ps1 -NetworkMode LOCAL_ONLY
```

`scripts/start.ps1` 是开发兼容入口；稳定版安装、服务、升级和回滚应使用原生 Runtime Host，不应把 `scripts/dev/*` 当作生产生命周期管理器。

## 测试与质量门禁

```powershell
# API 单测（默认不连接实时 ComfyUI）
pnpm run api:test

# Web TypeScript/Vitest
pnpm run web:test

# 完整检查：蓝图校验、Python 编译、OpenAPI/客户端生成、API、mypy、Ruff、审计、Web 构建与测试
pnpm run check
```

涉及真实浏览器流程的 Playwright 用例位于 [`tests/e2e`](tests/e2e)，测试环境会使用 Edge、Vite 和项目中配置的本地 API；涉及真实 GPU / ComfyUI / SAPI 的用例应按文件中的 UAT 说明单独执行，不要把模拟证据当作真实模型产物。

OpenAPI 与 TypeScript 客户端由 [`scripts/generate_client.py`](scripts/generate_client.py) 从 FastAPI 应用生成：

```powershell
python scripts\generate_client.py
```

生成结果为 [`docs/openapi/openapi.json`](docs/openapi/openapi.json) 和 [`apps/web/src/generated/api.ts`](apps/web/src/generated/api.ts)，不要直接手改生成文件。

## 生产部署、升级与恢复

生产发行使用原生 Runtime Host。发行包内包含私有 Python 3.12、锁定依赖、预编译应用、Web 构建、迁移、契约和带 SHA-256 的 release manifest；Node.js、pnpm、系统 Python、源码目录和模型权重不是运行时前置条件。

- [Windows 部署](docs/deployment/windows.md)：桌面模式、可信局域网服务、安装器、ModelRoot、服务和防火墙。
- [Linux 部署](docs/deployment/linux.md)：systemd、实例目录、服务账号与反向代理边界。
- [升级与恢复手册](docs/operations/upgrade-recovery-runbook.md)：升级、恢复集、候选验证和回滚。
- [构建发行包](packaging/README.md)：Windows / Linux 构建、签名、离线 wheelhouse 和可复现性。
- [Windows 路径合同](docs/deployment/windows-path-contract.md)：模型、媒体、上传和远程浏览器的路径边界。

典型 Runtime Host 运维命令：

```powershell
local-drama-host.exe status
local-drama-host.exe doctor
local-drama-host.exe verify-release
local-drama-host.exe upgrade --bundle <path-to-ldsupdate>
local-drama-host.exe rollback
```

升级流程会先停止 / 排空现有运行、校验发行包、创建配置与数据库恢复点、在副本上演练迁移、验证 API / Web / Worker，再原子切换 active release。回滚使用与目标代码匹配的恢复集，不使用 Alembic downgrade 代替恢复。

## 仓库导航

| 路径 | 内容 |
|---|---|
| `apps/web/src` | React 页面、工作区、feature、路由与生成客户端 |
| `apps/api/local_drama` | FastAPI、domain、application、infrastructure、平台适配器 |
| `apps/api/alembic` | 线性数据库迁移 |
| `cmd/runtime-host` | Go Runtime Host |
| `cmd/desktop-launcher` | Windows 桌面启动器 |
| `config` / `release` | 机器配置模板、模型锁定清单与版本身份 |
| `contracts` | 配置、运行时、发行清单与维护结果 schema |
| `scripts` | 开发、诊断、审计、UAT、证据和生成工具 |
| `tests/e2e` | Playwright 端到端测试 |
| `docs/decisions` | ADR 与架构决策 |
| `docs/plan` | 产品、架构和演进设计 |
| `docs/evidence` | 阶段与 UAT 证据 |

架构原则、阶段基线、当前迁移约束和历史验收报告分散在 `docs/`。其中历史报告会保留当时的版本号、测试数量和迁移 head；判断当前代码时，应以源码、锁文件、[`release/version.json`](release/version.json)、[`docs/release/migration-contract.json`](docs/release/migration-contract.json) 和本次运行的门禁结果为准。

## 当前明确边界

- 不提供云协作、云 Provider、外部音色商城、正版素材商城、市场分析、多租户或 G11 legacy 旧项目迁移。
- Ref2V、实际生产档位效果、剪映真机导入等能力仍依赖本机模型、ComfyUI / 剪映版本和真实设备验证；代码级能力门禁不等于每台机器都可生成。
- 发行包和源码仓库不替用户解决模型许可证、音频 / 素材授权或最终交付签字；相关证据需要在具体项目中维护。
- `LAN_SERVICE` 当前没有登录鉴权，只能部署在明确的可信网络边界内。

## 许可证与资源责任

仓库中的应用代码、配置 schema、迁移和文档不代表第三方模型、节点、音色、字体、音乐或素材的再分发许可。使用者须自行确认本机模型、ComfyUI 节点、声音参考、媒体和导出内容的许可证与授权证据。
