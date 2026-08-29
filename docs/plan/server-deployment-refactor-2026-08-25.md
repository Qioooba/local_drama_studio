# 服务器部署改造方案（Server Deployment Refactor）

日期：2026-08-25
状态：已实施
范围：`apps/api`（后端全部）、`apps/web`（前端构建与文案）、`scripts/`（启动脚本）

---

## 1. 背景与问题

项目首版按「本机单用户桌面工作台」设计（LOCAL_ONLY，仅绑定 127.0.0.1）。
现在需要在 **Windows 服务器** 上部署，并允许局域网内其他机器的浏览器：

1. 访问前端页面；
2. 上传媒体文件、剧本文档；
3. 实时预览视频（Range 流式播放）。

### 1.1 问题清单（分析结论）

| # | 位置 | 问题 | 影响 |
|---|------|------|------|
| P1 | `config.py:11,41-49` | `host` 校验器硬性禁止非 loopback 绑定 | API 无法在服务器上监听局域网地址 |
| P2 | `middleware.py:54-78` | 写请求 Origin 白名单只接受 loopback 字面量 | 远程浏览器所有 POST/PUT/PATCH/DELETE 被 403 拒绝 |
| P3 | `main.py` | 无 CORSMiddleware、无前端静态托管 | 生产部署必须外挂反向代理，且跨域场景完全不可用 |
| P4 | `config.py:21-29,53,130,134` | 全部数据根目录由 `REPO_ROOT` 推导、无环境变量覆盖 | 服务器上无法把 data/projects/work 放到独立磁盘 |
| P5 | `config.py:70,81` | ffmpeg 兜底路径硬编码 `E:\Tools\ffmpeg\bin\*` | 其他机器部署时静默失效；不可配置 |
| P6 | `health.py:76` | 直接读 env 绕过 Settings，与 `settings.ffmpeg_path` 逻辑重复 | 两套探测结果可能不一致 |
| P7 | `media.py:88-96` / `imports.py:42` | 上传大小限制硬编码（25/100/50 MB） | 服务器场景无法按磁盘/带宽调整 |
| P8 | `infrastructure/comfy.py:24`、`infrastructure/adapters.py:45`、`domain/policies.py:140` | ComfyUI/Runtime endpoint 强制 loopback | ComfyUI 部署在另一台 GPU 机器时无法接入 |
| P9 | `scripts/start.ps1:26,31` | 启动脚本硬编码 `--host 127.0.0.1` 且只探测 loopback 监听 | 服务器模式无法通过脚本启动 |
| P10 | `scripts/start_api.ps1` / `start_web.ps1` | 硬编码 `F:\AI_Projects\h3\local_drama_studio`；start_api 引用不存在的模块 `local_drama_studio_api.main`、端口 8000 | 换机器即失效，脚本本身就是坏的 |
| P11 | `apps/web/vite.config.ts` + `package.json` | 无生产伺服方案（无 preview 配置），proxy 目标仅 dev 可用 | 构建产物没有官方托管方式 |
| P12 | 前端 `ScriptImportPanel.tsx` 等 | 「从本机共享目录导入」「浏览…」（服务端弹窗选文件）未标注是**服务器**磁盘路径 | 远程用户误以为是选自己电脑的文件 |
| P13 | 前端整体 | API 层已全部使用相对路径（✅ 无需改）；视频 Range 流式、SSE、CSRF token 刷新均已就绪（✅） | — |

### 1.2 明确不改的部分（设计正确的部分）

- 前端所有 API/视频/缩略图 URL 均为同源相对路径 —— 远程访问天然兼容。
- `/media-versions/{id}/content|proxy` 已实现完整 HTTP Range/206/ETag/If-Range —— 远程实时预览天然兼容。
- `/projects/{id}/media:upload` 与 `/imports:upload` 为浏览器原始字节流上传 —— 远程上传天然兼容。
- CSRF（X-Local-Instance-Token + session/bootstrap 自动刷新重放）机制与传输方式无关。
- 自动化 webhook 的 loopback-only 约束保留（安全边界，不属于本次远程化目标）。
- LLM 已支持 OPENAI_COMPAT 远程 Provider（带 Bearer），无需改动。

---

## 2. 设计：双模式网络策略

引入一等配置项 **`LOCAL_DRAMA_NETWORK_MODE`**，两个合法值：

| | `LOCAL_ONLY`（默认） | `LAN_SERVICE` |
|---|---|---|
| API 绑定 | 仅 loopback（行为不变） | loopback / `0.0.0.0` / 具体 IP 均可 |
| 写请求 Origin 校验 | 配置 origins + loopback origin | 同上 + **同源请求**（Origin == Host 头） |
| CORS | 关闭（同源代理即可） | 开启，允许配置的 origins，暴露 X-Local-Instance-Token |
| ComfyUI/Runtime endpoint | 仅 loopback（不变） | loopback + RFC1918 私网地址；仍拒绝公网 |
| 前端静态托管 | 可用但非必需 | 推荐：API 直接托管 `apps/web/dist`（单端口同源） |
| 安全模型 | 本机单用户 | 局域网受信网段；**无登录鉴权，需用防火墙限定可信网段** |

关键原则：

1. **默认值 = 现状**。不设任何新环境变量时，系统行为与改造前逐字节一致，既有安全测试全部保持有效。
2. **fail-closed**。`LAN_SERVICE` 下放宽的每一项都仍有边界：私网地址段校验、Origin 必须同源或显式登记、公网地址永远拒绝。
3. **单一事实来源**。ffmpeg/ffprobe、上传上限等全部收敛到 `Settings`，路由层不再直接读 env。

## 3. 新增环境变量总表

### 3.1 网络
| 变量 | 默认 | 说明 |
|---|---|---|
| `LOCAL_DRAMA_NETWORK_MODE` | `LOCAL_ONLY` | `LOCAL_ONLY` / `LAN_SERVICE` |
| `LOCAL_DRAMA_HOST` | `127.0.0.1` | LAN_SERVICE 下可为 `0.0.0.0` 或具体 IP |
| `LOCAL_DRAMA_PORT` | `3210` | 不变 |
| `LOCAL_DRAMA_ALLOWED_ORIGINS` | （loopback 默认集） | 追加式白名单，逗号分隔 |

### 3.2 存储根目录（新增覆盖能力）
| 变量 | 默认 |
|---|---|
| `LOCAL_DRAMA_DATA_ROOT` | `<repo>/data` |
| `LOCAL_DRAMA_PROJECTS_ROOT` | `<repo>/projects` |
| `LOCAL_DRAMA_WORK_ROOT` | `<repo>/work` |
| `LOCAL_DRAMA_CACHE_ROOT` | `<repo>/cache` |
| `LOCAL_DRAMA_LOGS_ROOT` | `<repo>/logs` |
| `LOCAL_DRAMA_BACKUPS_ROOT` | `<repo>/backups` |

### 3.3 工具链
| 变量 | 默认 | 说明 |
|---|---|---|
| `LOCAL_DRAMA_FFMPEG` / `LOCAL_DRAMA_FFPROBE` | PATH 查找 | 最高优先级（原有） |
| `LOCAL_DRAMA_TOOL_FALLBACK_DIRS` | 空 | 替代原 `E:\Tools\...` 硬编码兜底；分号分隔目录，自动拼接 `ffmpeg.exe/ffprobe.exe` |
| `LOCAL_DRAMA_COMFY_BASE_URL` | `http://127.0.0.1:8188` | LAN_SERVICE 下可指向私网 GPU 机器 |
| `LOCAL_DRAMA_FRONTEND_DIST` | `<repo>/apps/web/dist` | 存在则由 API 托管前端 |

### 3.4 上传限制
| 变量 | 默认 |
|---|---|
| `LOCAL_DRAMA_UPLOAD_MAX_IMAGE_MB` | `25` |
| `LOCAL_DRAMA_UPLOAD_MAX_VIDEO_MB` | `100` |
| `LOCAL_DRAMA_UPLOAD_MAX_AUDIO_MB` | `50` |
| `LOCAL_DRAMA_UPLOAD_MAX_DOCUMENT_MB` | `25` |
| `LOCAL_DRAMA_UPLOAD_MAX_PROJECT_RESOURCE_MB` | `50` |
| `LOCAL_DRAMA_UPLOAD_MAX_PROJECT_PACKAGE_MB` | `2048` |

### 3.5 服务端模型库

| 变量 | 默认 | 说明 |
|---|---|---|
| `LOCAL_DRAMA_MODEL_LIBRARY_ROOTS` | 空 | Windows 服务端允许扫描的模型根目录，多个目录用分号分隔。LAN 客户端不能提交任意绝对路径，只能从这些受控根目录选择。 |

## 4. 分项实施内容

### 4.1 后端

- **config.py**：
  - `network_mode` 字段 + 校验；
  - host 校验按模式分流；
  - 六个根目录字段支持 env 覆盖；
  - `ffmpeg_path`/`ffprobe_path` 收敛为纯 Settings 逻辑（env > PATH > fallback dirs）；
  - 上传上限字段；
  - `frontend_dist_root` 字段；
  - `allows_private_network` 辅助属性。
- **middleware.py**：`LocalOriginMiddleware` 增加 same-origin 校验分支（仅 LAN_SERVICE 生效），错误信息区分提示。
- **main.py**：挂载 `CORSMiddleware`（expose `X-Local-Instance-Token`、`ETag`、`Content-Range`）；dist 存在时托管静态前端 + SPA fallback（`/api` 前缀永不落入 fallback）。
- **infrastructure/comfy.py**：`ComfyClient(allow_private_network=...)`，LAN 模式放行 RFC1918/loopback，拒绝公网与环回以外的非常规地址；三个实例化点传入 settings。
- **infrastructure/adapters.py**：`validate_adapter_target(..., allow_private_network=...)`，registry 按 settings 放宽并如实上报 `mode`。
- **domain/policies.py**：`validate_local_transport` 增加可选 `allow_private_network` 参数（保持向后兼容）。
- **health.py**：dependencies 检查改用 `settings.ffmpeg_path`。
- **media.py / imports.py**：上传上限从 settings 读取。

### 4.2 启动脚本

- `scripts/start.ps1`：`-HostAddress` / `-NetworkMode` 参数化；监听探测按实际绑定地址匹配；启动前导出 `LOCAL_DRAMA_NETWORK_MODE/HOST`。
- `scripts/start_api.ps1`：改为 `$PSScriptRoot` 相对定位；修正模块名 `local_drama.main:app`、端口 3210；host 从环境变量读取。
- `scripts/start_web.ps1`：改为 `$PSScriptRoot` 相对定位；host/port 可参数化。

### 4.3 前端

- `vite.config.ts`：增加 `preview` 配置（host/proxy 与 server 对齐，`LOCAL_DRAMA_API_PROXY` 复用）。
- `package.json`：新增 `preview` script。
- 剧本、媒体、LUT、授权证据和 `.ldspkg` 项目包都以浏览器文件上传为主入口；选择的是当前 Mac/PC 上的文件，服务端以流式、限额、临时文件加原子落盘的方式接收。
- Windows 文件对话框和绝对路径输入只对服务端 loopback 浏览器显示；LAN 浏览器不会远程弹出服务器桌面窗口。
- 模型权重不经浏览器上传。管理员用 `LOCAL_DRAMA_MODEL_LIBRARY_ROOTS` 配置受控服务端模型库，客户端只选择/扫描这些根目录。
- 时间线交换包、联系表、交付包和项目包均提供浏览器下载；页面仍展示服务端相对路径用于追溯，但不要求远程用户进入服务器磁盘取文件。
- 图片、音频和视频预览统一通过媒体 HTTP 端点；视频继续使用 Range/206、代理优先和原片回退，避免把服务器路径暴露为客户端可打开的地址。

### 4.4 测试（新增 `apps/api/tests/test_server_deployment.py`）

- LOCAL_ONLY 回归：host 拒绝 `0.0.0.0`、写 Origin 行为不变（防回归锚点）。
- LAN_SERVICE：绑定地址放宽；same-origin 写放行；外部 origin 仍拒绝；CORS 头存在。
- ComfyClient：LAN 下接受 `http://192.168.x.x:8188`、拒绝公网；LOCAL 下维持拒绝。
- 上限配置化生效。
- ffmpeg fallback 目录生效。
- 静态托管：dist 存在时 `/` 返回 index.html、SPA fallback、`/api` 不受影响。

## 5. 部署手册（Windows 服务器）

```powershell
# 一次性
# 持久部署优先修改 config/config.json（SERVER + LAN_SERVICE）；
# 下列环境变量仅用于当前进程的临时覆盖。
$env:LOCAL_DRAMA_NETWORK_MODE = "LAN_SERVICE"
$env:LOCAL_DRAMA_HOST         = "0.0.0.0"
$env:LOCAL_DRAMA_PORT         = "3210"
# 数据盘迁移示例
$env:LOCAL_DRAMA_DATA_ROOT    = "D:\lds\data"
$env:LOCAL_DRAMA_PROJECTS_ROOT= "D:\lds\projects"
$env:LOCAL_DRAMA_WORK_ROOT    = "D:\lds\work"
# 工具链
$env:LOCAL_DRAMA_FFMPEG       = "D:\tools\ffmpeg\bin\ffmpeg.exe"
$env:LOCAL_DRAMA_FFPROBE      = "D:\tools\ffmpeg\bin\ffprobe.exe"
# 远程 ComfyUI（另一台 GPU 机器）
$env:LOCAL_DRAMA_COMFY_BASE_URL = "http://192.168.1.101:8188"
# 受控模型库（多个根目录用分号分隔）
$env:LOCAL_DRAMA_MODEL_LIBRARY_ROOTS = "D:\AI\Models;E:\SharedModels"
# 可按局域网带宽与文件规模调整
$env:LOCAL_DRAMA_UPLOAD_MAX_PROJECT_RESOURCE_MB = "50"
$env:LOCAL_DRAMA_UPLOAD_MAX_PROJECT_PACKAGE_MB  = "2048"

cd F:\AI_Projects\h3\local_drama_studio
pnpm --filter local-drama-studio-web build   # 产出 apps/web/dist
.\scripts\start.ps1                          # 默认读取 config/config.json
```

客户端浏览器访问 `http://<server-ip>:3210` 即可完成页面、上传、实时预览全流程。
防火墙仅需放行 3210；API 自身同时服务前端与后端（同源，无跨域问题）。

开发模式（跨机调试 Vite）：`start_web.ps1` 已绑 `0.0.0.0`，远程机器访问 `http://<dev-ip>:5173`，
Vite proxy 把 `/api` 转发到本机 API；此时需把 `http://<dev-ip>:5173` 加入
`LOCAL_DRAMA_ALLOWED_ORIGINS`（Vite proxy 模式下请求经代理转发到 API 时 Origin 会保留浏览器源）。

> 安全提醒：`LAN_SERVICE` 模式没有登录鉴权（沿用单用户信任模型 + CSRF token）。
> 请通过 Windows 防火墙将 3210 限制在可信网段，或在前置 Nginx/Caddy 加 Basic Auth。

## 6. 风险与回滚

- 所有改动以 `LOCAL_DRAMA_NETWORK_MODE=LOCAL_ONLY`（缺省）保持旧行为；出问题时删除该环境变量重启即回滚。
- 新增测试锚定旧行为（P1/P2 相关断言原样保留在新测试文件中）。
