# LocalDramaStudio Windows 首发、Linux 兼容的运行时与发布架构详细设计

- 状态：PROPOSED / 待 ADR 评审后实施
- 日期：2026-08-26
- 目标版本：Windows x64 首个可安装稳定版；Linux x64 服务版紧随其后
- 适用范围：运行时宿主、平台适配、配置、进程、存储、数据库维护、构建、安装、升级、回滚、发布验证
- 不改变的业务真值：现有 Domain、Application、Job、MediaVersion、Review、Timeline、Audit 与 Alembic 事实链
- 取代范围：本文取代“继续增强 `scripts/start*.ps1` 作为生产入口”的路线；`server-deployment-refactor-2026-08-25.md` 仅继续作为当前 LAN 网络行为的历史实现记录

---

## 1. 结论与强制决策

本次工作不是为现有 PowerShell 启动脚本继续增加条件分支，而是建立新的产品运行时与发布边界。

必须执行以下架构决策：

1. 保留“React Web + FastAPI API + Python Worker + SQLite/WAL + 项目文件系统”的模块化单体，不拆微服务，不另建业务任务系统。
2. 引入独立的跨平台 Runtime Host。Host 只负责安装实例、单实例锁、子进程托管、健康检查、版本选择、升级和回滚，不承载业务逻辑。
3. Windows 首发实现原生 Runtime Host 和 Windows Service/交互桌面两种运行模式；Linux 复用同一 Host 协议，以 systemd 运行。
4. 业务代码不得直接调用 PowerShell、WinForms、System.Speech、Windows Credential Manager 或平台服务 API；全部通过 Platform Ports 调用。
5. 代码目录与用户数据目录彻底分离。版本目录只读；数据库、项目、配置、日志、备份和升级状态永远位于版本目录之外。
6. 正式启动不再自动执行未保护的数据库迁移。首次安装和升级只能走 Maintenance/Updater 的“预检—备份—副本演练—正式迁移—验证”状态机。
7. 正式发布采用 versioned onedir，不采用“所有内容压成一个临时解包的单文件 EXE”。用户可以只看到一个入口 EXE，但内部必须保留可检查、可版本切换的目录结构。
8. 生产运行不依赖系统 Python、Node 或 pnpm。Node/pnpm 只存在于构建环境；Python 运行时随平台发行物提供。
9. Windows 与 Linux 使用同一份 Python 业务 wheel、同一份 Web dist、同一套数据库 migration 和发布清单 schema；只替换 Host 二进制和 Platform Adapter。
10. 跨平台兼容不是以后再打补丁。所有新路径、配置、凭据、外部模型引用和进程生命周期从第一期即使用平台无关合同。

### 1.1 首发产品形态

Windows 提供两种安装配置，但共享同一安装包和业务核心：

| 配置 | 进程身份 | 网络 | 交互能力 | 用途 |
|---|---|---|---|---|
| DESKTOP | 当前登录用户 | 默认 loopback | 文件选择器、当前用户凭据、本机 SAPI | 个人工作站 |
| SERVER | 专用低权限服务账户 | loopback 或受控 LAN | 无桌面弹窗；浏览器上传；服务账户能力 | 无人值守 Windows 服务器 |

Linux 首期只提供 SERVER 配置。Linux 桌面壳不是本次目标，浏览器即客户端。

### 1.2 不选择的方向

| 方向 | 决策 | 原因 |
|---|---|---|
| 继续扩展 PowerShell 生产脚本 | 拒绝 | 无跨平台生命周期合同，进程守护、升级和错误恢复不可验证 |
| Electron 重做桌面端 | 拒绝 | 重复打包 Chromium/Node，增加内存和升级面；现有 Web 已可由 API 同源托管 |
| Docker 作为唯一发行物 | 拒绝 | 不适合 Windows 个人桌面、SAPI、Credential Manager 和本机 GPU/文件交互；可作为 Linux 可选发行物 |
| 全部做成 PyInstaller onefile | 拒绝 | 启动需临时解包、杀毒扫描面大、资源与 migration 难检查、运行中无法安全覆盖 |
| 直接微服务化/API+Worker 独立数据库 | 拒绝 | 当前单机/单服务器规模无收益，会破坏事务与部署简单性 |
| 为 Linux 复制一套业务实现 | 拒绝 | 必须共用 Domain/Application/Schema，只允许平台 adapter 不同 |
| 立即切 PostgreSQL/Redis/Celery | 拒绝 | 当前单节点 SQLite/WAL + durable jobs 已满足目标；先通过规模门禁再决定 |

---

## 2. 当前事实与必须重构的问题

### 2.1 已具备、必须保留

- FastAPI 可托管 Web `dist`，生产运行无需 Vite server。
- 前端 API URL 以同源相对路径为主，适合桌面和 LAN。
- SQLite 已使用 WAL、foreign keys、busy timeout。
- Alembic graph 与 migration contract 已有自动测试。
- `online_backup` 已使用 SQLite Backup API，并对备份执行 `integrity_check`。
- Worker 已有 durable session、heartbeat、lease、reconcile 和 API/Worker 版本握手。
- 媒体内容以项目文件系统为权威，数据库以关系事实为权威。
- data/projects/work/cache/logs/backups 已具备环境变量覆盖能力。
- FFmpeg/FFprobe 与 ComfyUI/LLM 已存在可配置 endpoint 或路径基础。

### 2.2 当前结构性问题

1. `scripts/start.ps1` 直接执行 `alembic upgrade head`，没有调用已有的安全备份迁移入口。
2. `.venv` 缺失时启动脚本会使用任意 PATH Python，运行环境不确定。
3. Web 安装命令允许 lockfile 漂移，构建不是严格可复现。
4. API、Worker、dist、migration、配置和数据默认依赖仓库相对目录。
5. `REPO_ROOT = Path(__file__).parents[...]` 同时承担源码根、资源根和默认数据根；冻结后语义失效。
6. Runtime Host 不存在，PowerShell 父进程退出后没有 OS 级监督和重启预算。
7. 启停状态以可被截断的 PID JSON 表示，没有跨进程锁、原子版本选择和 IPC 控制面。
8. 应用版本分别存在于 Python package、Settings、Web package 和 SBOM，无法证明同一构建身份。
9. Windows 文件选择器、SAPI 和 Credential Manager 直接进入 application/infrastructure 调用路径。
10. LAN_SERVICE 仍是可信局域网无登录模型，不适合作为默认可暴露服务。
11. 数据库备份没有与项目媒体文件树形成同一备份集，无法证明完整恢复点。
12. 当前“升级”依赖人工替换源码和匹配代码版本，不具备下载验证、版本槽、失败回滚或断电恢复。

### 2.3 重构边界

本次不推倒已有业务模块。重构围绕六个横切面：

```text
Runtime Host
  ├─ Process lifecycle
  ├─ Install/release selection
  ├─ Upgrade/rollback
  └─ OS service integration

Python Application Runtime
  ├─ Bootstrap/config/resource location
  ├─ API entrypoint
  ├─ Worker entrypoint
  ├─ Maintenance entrypoint
  └─ Platform ports/adapters

Release System
  ├─ Reproducible build
  ├─ Versioned payload
  ├─ Signed manifest
  ├─ Installer
  └─ Installed-artifact verification
```

Domain 和 Application 只在发现平台调用泄漏时调整依赖方向，不重写业务表和流程。

---

## 3. 目标架构

### 3.1 逻辑视图

```text
Browser
   │ HTTP(S), same origin
   ▼
API process ────────────────┐
   │                         │
   │ short SQLite tx         │ local control IPC
   ▼                         ▼
SQLite/WAL              Runtime Host
   ▲                         │
   │ lease/heartbeat         ├─ starts/stops API
   │                         ├─ starts/stops Worker
Worker process               ├─ health/restart budget
   │                         ├─ active release pointer
   ├─ FFmpeg/FFprobe         └─ updater handoff
   ├─ ComfyUI/LLM/TTS
   └─ project/work/cache filesystem
```

### 3.2 物理进程

稳定版固定为以下进程角色：

| 角色 | 数量 | 职责 | 禁止事项 |
|---|---:|---|---|
| Runtime Host | 1 | 单实例、子进程、健康、升级、服务集成 | 不读写业务表，不执行生成逻辑 |
| API | 1 | HTTP、静态前端、短事务、控制命令 | 不执行长 FFmpeg/GPU/模型任务 |
| Worker | 默认 1 | durable jobs、媒体、TTS、Comfy/LLM | 不提供公共 HTTP |
| Maintenance | 按需短进程 | 安装、迁移、备份、恢复、诊断、验证 | 不与 RUNNING 实例同时修改数据库 |
| Optional GPU runtime | 外部 | ComfyUI/Ollama/其他模型运行时 | 不成为应用升级的一部分 |

SQLite 模式下 API 不启动多个 Uvicorn worker。多 Uvicorn 进程只会增加 SQLite 写竞争、生命周期复杂度和内存，不会改善当前主要瓶颈。并发通过异步 I/O、短事务和 durable Worker 扩展。

### 3.3 Host 与业务运行时的边界

Runtime Host 推荐使用 Go 实现为极小的跨平台控制平面，业务仍保持 Python：

- Host 必须能在 Python payload 损坏或迁移失败时继续工作。
- Host 二进制单文件、启动快、依赖少，适合 Windows SCM 和 Linux systemd。
- Host 不复制 Pydantic 业务配置，只解析 bootstrap 子集。
- Host 不接触业务数据库内容，只调用 Maintenance 命令并读取机器可验证的 JSON 结果。
- Go 中不得实现项目、媒体、Job、Profile 等领域逻辑。

固定 Host 与版本 payload 分离。普通应用更新只增加/删除 version 目录；只有 Host 协议升级才要求运行完整安装器。

### 3.4 Host 协议

版本化 Host 协议定义在 `contracts/runtime-host.schema.json`：

```json
{
  "protocol_version": 1,
  "host_version": "1.0.0",
  "release_version": "1.0.0",
  "role": "api",
  "instance_id": "default",
  "config_path": ".../config.json",
  "release_root": ".../versions/1.0.0",
  "data_root": ".../data",
  "control_endpoint": "platform-specific-local-ipc"
}
```

Host 向子进程传递短期 bootstrap JSON 文件或环境变量指针，不在命令行暴露 secret。API/Worker 启动后通过本地 IPC 或受保护的 loopback management endpoint 报告：

- build identity；
- migration revision；
- config fingerprint；
- role readiness；
- Worker protocol compatibility；
- drain/stop 状态。

Windows 使用 Named Pipe；Linux 使用 Unix Domain Socket。公共 HTTP API 不承担安装和版本切换控制面。

---

## 4. 目标仓库结构

```text
apps/
  api/
    pyproject.toml
    src/local_drama/
      api/
      application/
      domain/
      infrastructure/
      bootstrap/
        config_loader.py
        resource_locator.py
        build_identity.py
        composition.py
      entrypoints/
        api.py
        worker.py
        maintenance.py
      platform/
        contracts.py
        registry.py
        common/
        windows/
          credentials.py
          file_picker.py
          tts.py
          filesystem.py
        linux/
          credentials.py
          file_picker.py
          tts.py
          filesystem.py
  web/

cmd/
  runtime-host/              # Go；只含部署控制平面

contracts/
  config.schema.json
  release-manifest.schema.json
  runtime-host.schema.json
  maintenance-result.schema.json

packaging/
  common/
  windows/
    installer/
    service/
    build.ps1
  linux/
    systemd/
    build.sh
    container/

release/
  version.json               # 唯一版本事实来源
  compatibility.json

scripts/
  dev/                       # 仅开发辅助，不能作为生产入口
  ci/

tests/
  packaging/
  upgrade/
  platform/
```

### 4.1 Python `src/` layout

API package 迁入 `apps/api/src/local_drama`，避免当前工作目录意外影响 import。所有生产入口必须从已安装 wheel 或冻结 payload 导入，禁止依赖 `--app-dir apps/api` 和 `sys.path.insert`。

### 4.2 唯一版本来源

新增 `release/version.json`：

```json
{
  "version": "1.0.0",
  "channel": "stable",
  "host_protocol": 1,
  "worker_protocol": 1,
  "config_schema": 1,
  "project_package_schema": 2
}
```

构建阶段由生成器写入：

- Python package version；
- `local_drama.__version__`；
- Web build constant；
- OpenAPI version；
- Worker handshake version；
- SBOM version；
- release manifest version；
- 安装器版本。

源码中禁止再次硬编码产品版本。CI 检查生成结果与 `release/version.json` 一致。

---

## 5. 平台抽象设计

### 5.1 Platform Ports

`platform/contracts.py` 定义以下协议：

```python
class SecretStore(Protocol):
    def get(self, ref: SecretRef) -> str | None: ...
    def put(self, ref: SecretRef, value: str) -> None: ...
    def delete(self, ref: SecretRef) -> bool: ...

class FilePicker(Protocol):
    @property
    def available(self) -> bool: ...
    def choose_file(self, request: FilePickerRequest) -> Path | None: ...

class TtsRuntime(Protocol):
    def capabilities(self) -> TtsCapabilities: ...
    def list_voices(self) -> list[VoiceDescriptor]: ...
    def synthesize(self, request: TtsRequest, destination: Path) -> TtsEvidence: ...

class PlatformFilesystem(Protocol):
    def app_data_locations(self, scope: InstallScope) -> PlatformLocations: ...
    def atomic_replace(self, source: Path, destination: Path) -> None: ...
    def acquire_instance_lock(self, name: str) -> InstanceLock: ...
    def is_local_fixed_storage(self, path: Path) -> bool: ...

class PlatformIdentity(Protocol):
    def service_mode(self) -> bool: ...
    def interactive_session(self) -> bool: ...
    def identity_summary(self) -> IdentitySummary: ...
```

Application service 只能依赖这些 Protocol 或更高层能力服务。Adapter 的异常必须映射为稳定的领域/运行时错误码。

### 5.2 Windows adapters

| Port | Windows DESKTOP | Windows SERVER |
|---|---|---|
| SecretStore | 当前用户 Credential Manager | 服务账户 Credential Manager；由运行中的服务写入 |
| FilePicker | Win32/WinForms adapter，可用 | `UnavailableFilePicker`，API 引导浏览器上传 |
| TtsRuntime | Windows SAPI adapter | 服务账户下探测；无可用 voice 时显式 UNAVAILABLE |
| Filesystem | NTFS/ReFS 语义、Windows 原子 replace | 相同，增加服务 SID ACL 验证 |
| Process/Service | 交互 Host | Windows SCM Host |

SAPI、Credential Manager 和文件选择器不能再由 `dialogue.py`、`local_picker.py` 或 provider service 直接执行 PowerShell/ctypes。Windows adapter 优先使用直接 Python/Win32 API；仅在确无稳定 API 时允许受控 subprocess，并必须隔离在 adapter 内。

### 5.3 Linux adapters

| Port | Linux SERVER |
|---|---|
| SecretStore | systemd credential/file adapter；0600、服务用户所有；可选 Secret Service |
| FilePicker | 永远不可用，浏览器上传 |
| TtsRuntime | `UnavailableTtsRuntime` 或明确安装的 Linux TTS Provider adapter |
| Filesystem | ext4/XFS 本地存储、POSIX lock/rename/fsync |
| Process/Service | systemd foreground process + watchdog |

Linux 未安装某项 adapter 时，功能状态必须是 `UNAVAILABLE`，API 和非相关 Worker 仍可启动。平台能力不得通过 import failure 使整个应用退出。

### 5.4 平台能力发现

新增 `/api/v1/system/platform-capabilities`，返回非敏感能力：

```json
{
  "os": "windows",
  "arch": "amd64",
  "install_profile": "SERVER",
  "interactive": false,
  "capabilities": {
    "native_file_picker": "UNAVAILABLE_BY_PROFILE",
    "local_tts": "AVAILABLE",
    "secret_store": "AVAILABLE",
    "ffmpeg": "AVAILABLE",
    "service_supervision": "AVAILABLE"
  }
}
```

前端按 capability 隐藏或替换动作，不读取 User-Agent 猜平台。

---

## 6. 配置系统

### 6.1 配置事实来源

新增 `config.json`，由 JSON Schema + Pydantic 双重验证。选择 JSON 而不是继续使用散落环境变量，原因是：

- Go Host 与 Python Runtime 可用同一 schema；
- 可原子替换、做版本迁移和 fingerprint；
- Windows/Linux 行为一致；
- 不引入额外 TOML 写入器；
- 机器配置与业务数据库分离，数据库损坏时仍可恢复。

配置优先级固定为：

```text
compiled safe defaults
  < config.json
  < environment overrides（容器/运维）
  < explicit CLI one-shot overrides（诊断/测试）
```

Secret 值不得写入 `config.json`。文件只保存 `secret_ref`。

### 6.2 配置示例

```json
{
  "schema_version": 1,
  "instance_id": "default",
  "install_profile": "SERVER",
  "network": {
    "mode": "LAN_SERVICE",
    "host": "0.0.0.0",
    "port": 3210,
    "allowed_origins": [],
    "trusted_lan_unauthenticated": false
  },
  "storage": {
    "data_root": "D:/LocalDramaStudio/data",
    "projects_root": "D:/LocalDramaStudio/projects",
    "work_root": "D:/LocalDramaStudio/work",
    "cache_root": "D:/LocalDramaStudio/cache",
    "logs_root": "D:/LocalDramaStudio/logs",
    "backups_root": "E:/LocalDramaStudioBackups"
  },
  "tools": {
    "ffmpeg": "${RELEASE_ROOT}/tools/ffmpeg/ffmpeg.exe",
    "ffprobe": "${RELEASE_ROOT}/tools/ffmpeg/ffprobe.exe"
  },
  "runtime": {
    "comfy_base_url": "http://127.0.0.1:8188",
    "worker_channels": ["CPU", "GPU_H3"],
    "cpu_worker_concurrency": 1,
    "gpu_worker_concurrency": 1
  },
  "backup": {
    "before_upgrade": true,
    "daily_retention": 7,
    "weekly_retention": 4
  }
}
```

### 6.3 路径语义

- 配置输入接受平台原生绝对路径。
- 数据库中的项目媒体路径继续只保存受控根下的 POSIX 风格相对路径。
- `RELEASE_ROOT`、`INSTANCE_ROOT` 等变量由 ResourceLocator 展开，不依赖当前工作目录。
- 所有路径在启动预检时 canonicalize，并验证 reparse point/symlink、ACL、剩余空间和本地磁盘性质。
- 数据库不得位于 SMB/NFS/同步盘；检测到时 fail closed。
- `work` 中需要原子提交到 `projects` 的 staging 必须位于目标项目同一 volume。跨卷提交使用“复制—fsync—hash—原子 rename”的明确协议，不能假设 `replace` 原子。

### 6.4 外部模型与跨平台资源引用

Windows 绝对模型路径不能成为跨平台身份。新增逻辑资源引用：

```json
{
  "resource_id": "model:minimax-h3:sha256:...",
  "kind": "MODEL",
  "sha256": "...",
  "logical_name": "MiniMax H3",
  "location_alias": "primary-model-library",
  "observed_platform": "windows-amd64",
  "observed_path": "D:/AI/Models/..."
}
```

Profile/Job 快照冻结 content identity 和 capability，不把机器绝对路径作为模型身份。切换 Windows/Linux 后，管理员通过 hash 在本机 model library 重绑定 location。历史 Job 仍保留原平台/原路径证据，但运行时解析使用当前 platform binding。

---

## 7. 安装目录与数据目录

### 7.1 Windows SERVER

```text
C:\Program Files\LocalDramaStudio\
  host\
    LocalDramaStudio.Host.exe
    host-version.json
  versions\
    1.0.0\
      runtime\
      app\
      web\
      migrations\
      tools\
      release-manifest.json
  active-release.json

C:\ProgramData\LocalDramaStudio\
  config\config.json
  data\
  projects\
  work\
  cache\
  logs\
  backups\
  runtime\
  updates\
```

数据盘可通过安装向导移至 D:/E:，但 config 和最小 runtime state 保留在 ProgramData。

### 7.2 Windows DESKTOP

代码默认安装到 `%LOCALAPPDATA%\Programs\LocalDramaStudio`，数据默认位于 `%LOCALAPPDATA%\LocalDramaStudio`。选择 all-users 安装时才使用 Program Files/ProgramData。

### 7.3 Linux SERVER

```text
/opt/local-drama-studio/
  host/
  versions/
  active-release.json

/etc/local-drama-studio/config.json
/var/lib/local-drama-studio/{data,projects,work,cache,backups,runtime,updates}
/var/log/local-drama-studio/
```

### 7.4 active release

`active-release.json` 只含版本选择事实，不含业务状态：

```json
{
  "schema_version": 1,
  "active": "1.0.0",
  "previous": null,
  "generation": 1,
  "updated_at": "2026-08-26T00:00:00Z"
}
```

文件通过同目录临时文件、flush/fsync、atomic replace 更新。Host 读取后还必须验证目标 release manifest 和完整性，不能盲信路径。

---

## 8. 启动、停止与故障恢复

### 8.1 Host 状态机

```text
STOPPED
  → ACQUIRE_INSTANCE_LOCK
  → LOAD_ACTIVE_RELEASE
  → VERIFY_RELEASE
  → LOAD_CONFIG
  → PREFLIGHT_STORAGE
  → VERIFY_DATABASE_COMPATIBILITY
  → START_API
  → WAIT_API_READY
  → START_WORKER
  → WAIT_WORKER_READY
  → RUNNING

RUNNING
  → DRAINING
  → STOP_WORKER
  → STOP_API
  → STOPPED

任意阶段失败
  → CAPTURE_DIAGNOSTICS
  → bounded retry 或 FAILED
```

### 8.2 单实例

- Windows：Named Mutex + lock file identity。
- Linux：`flock`/Unix lock file。
- lock 内容记录 instance id、Host PID、start time、release、boot id。
- 不依赖 PID 文件判断唯一性；PID 文件仅用于诊断。
- 检测 PID reuse 时以 lock ownership 和进程启动时间为准。

### 8.3 Readiness

API readiness 必须同时满足：

- release identity 与 Host 期望一致；
- config schema 可用；
- 数据目录可读写；
- SQLite integrity quick check 通过；
- 当前 migration revision 在 release compatibility 范围内；
- 前端 index 与 asset manifest 存在；
- public port 已绑定。

Worker readiness 必须满足：

- API/Worker release 与 protocol compatible；
- durable session 已创建；
- heartbeat 可持续；
- 配置 channel 可加载；
- 缺失可选 runtime 只降级相应 capability，不阻止 CPU channel。

### 8.4 重启策略

Host 只处理“进程级故障”；Worker 内部 supervisor 处理“Job 执行级故障”，两层不能重复重试同一语义。

- API：60 秒窗口最多重启 3 次，指数退避；继续失败进入 FAILED。
- Worker：进程级重启最多 5 次；启动前执行 stale session/storage reconcile。
- migration/incompatible/config error：永不自动循环重启，直接 fail closed。
- Host 退出码分为 CONFIG、RELEASE_INTEGRITY、DB_INCOMPATIBLE、CHILD_CRASH_LOOP、UPGRADE_REQUIRED。

### 8.5 优雅停止

1. Host 请求 API 进入 maintenance/draining。
2. API 拒绝新的生成/升级冲突命令，读请求继续。
3. Worker 停止 claim 新 Job；允许当前安全可完成步骤结束。
4. 到达 timeout 后，将有外部副作用的不确定任务标记 NEEDS_ATTENTION，不直接重试。
5. Worker 关闭，API 关闭，Host 释放锁。

---

## 9. 数据库与存储设计

### 9.1 SQLite 保留条件

首版继续 SQLite/WAL，约束为：

- 单安装实例；
- 单 API 进程；
- 数据库位于本机可靠文件系统；
- 写事务短小；
- FFmpeg、hash、模型调用和网络 I/O 全部在事务外；
- Job/lease 继续作为跨进程协调事实；
- 规模门禁覆盖 60 集、800+ 镜头、10,000+ 媒体记录和实际目标并发。

只有出现以下任一事实后才提交 PostgreSQL ADR：

- 多台应用服务器同时写；
- 单机写锁等待持续超过 SLO；
- 需要数据库级远程 HA；
- 单项目/全库规模超过已验证上限且索引/查询优化仍无法达标。

### 9.2 Connection policy

统一 `DatabaseRuntime` 创建连接并设置：

- `foreign_keys=ON`；
- `journal_mode=WAL`；
- `busy_timeout`；
- 明确且经断电测试的 `synchronous` policy；
- 统一 transaction helper；
- 读写耗时与 busy retry 指标。

不得在 route/application 中自行 `sqlite3.connect`。默认不引入长连接池；先通过基准证明连接建立是瓶颈后再调整。

### 9.3 migration policy

正式 API/Worker 永不执行 Alembic upgrade。Maintenance 提供：

```text
maintenance db inspect
maintenance db backup
maintenance db rehearse-upgrade
maintenance db upgrade
maintenance db verify
maintenance db restore
```

每个命令输出符合 `maintenance-result.schema.json` 的 JSON，并使用明确退出码。Updater 只根据 schema 字段决策，不解析控制台文本。

### 9.4 一致性备份集

备份单位不是单个 sqlite 文件，而是 Recovery Set：

```text
recovery-sets/20260826T010203Z/
  recovery-manifest.json
  database/local_drama.sqlite3
  config/config.json
  projects-manifest.jsonl
  projects/                 # full 或 incremental payload
  logs/upgrade-session.jsonl
```

Recovery Set 建立过程：

1. Host 进入 maintenance lock 并 drain Worker。
2. SQLite online backup + integrity check。
3. 捕获 migration heads、release version、config fingerprint。
4. 对项目权威文件生成路径、大小、mtime、sha256 清单；正式发布采用可恢复的 full/incremental 策略。
5. 写 manifest 临时文件并 fsync。
6. 完成后原子标记 `COMPLETE`。

不完整 Recovery Set 永远不能用于自动回滚。

### 9.5 cache/work 生命周期

- cache 可以按 release/cache schema 清理，不进入强制恢复集。
- work 中 durable Job 尚未提交的 staging 由 storage reconciler 接管。
- runtime 只含锁、socket、短期状态，启动时可重建。
- logs 按大小和天数滚动；升级会话日志至少保留到对应 recovery set 过期。

---

## 10. 性能设计

### 10.1 性能原则

1. 优化真实瓶颈：媒体 I/O、FFmpeg、模型 runtime、SQLite 写锁和大文件网络传输；不以“改成更多进程/语言”冒充性能优化。
2. API 只做短 CPU/DB 工作，所有长任务进入 durable Worker。
3. 所有大文件处理必须流式，内存占用不得随文件大小线性增长。
4. 项目内提交优先同卷 staging + atomic rename，避免重复复制大型视频。
5. 前端生产资源带内容 hash 和长期缓存；`index.html` no-cache，hashed assets immutable。
6. 视频/音频继续支持 Range、ETag、If-Range；代理文件由 Worker 异步生成。
7. GPU channel 默认独占并发 1；CPU concurrency 必须按任务类型和磁盘吞吐配置，而不是盲目按核心数拉满。

### 10.2 Python 运行时

- Windows/Linux 发行物包含固定 Python 3.12 patch 版本和锁定 wheel。
- 禁止生产环境 editable install。
- API 使用一个 Uvicorn process；loop 实现以平台兼容和基准结果决定，不把可选 native loop 作为正确性前提。
- subprocess 一律 `shell=False`，路径由 ToolRegistry 解析。
- FFmpeg stdout/stderr 有上限捕获或文件重定向，避免大输出占满内存。
- hash/probe/thumbnail 进入 Worker，route 不同步扫描大文件。

### 10.3 I/O 目标

在发布基准机上建立以下门禁，具体数值可通过首次基线微调，但不得删除指标：

| 指标 | 初始目标 |
|---|---:|
| 无 migration 的冷启动到 API ready | ≤ 8 秒 |
| API ready 到 Worker ready | ≤ 5 秒 |
| 10,000 media metadata 常用读路径 p95 | ≤ 200 ms |
| 1 GB 上传额外进程内存 | ≤ 128 MB |
| Range 播放首字节（本机代理文件）p95 | ≤ 500 ms |
| Host idle RSS | ≤ 40 MB |
| API idle RSS | ≤ 300 MB |
| Worker idle RSS | ≤ 250 MB |

模型生成时间不计入应用启动/HTTP SLO，但必须单独记录 queue、prepare、provider、download、postprocess 各阶段耗时。

### 10.4 背压

- 上传限制在读 body 前校验 Content-Length，并对 chunked upload 实施累计上限。
- 每种 channel 有队列上限和可观测等待时间。
- API 不允许无限并发启动 hash/FFmpeg 子进程。
- 磁盘剩余低于安全线时拒绝新生成和升级，继续允许导出/清理/备份诊断。

---

## 11. Windows 首发实现

### 11.1 Build artifact

Windows release payload：

```text
LocalDramaStudio-1.0.0-windows-amd64/
  runtime/                   # private CPython + locked site-packages
  app/                       # installed local_drama wheel
  web/                       # Vite production dist
  migrations/               # Alembic scripts + contract
  tools/                     # approved optional binaries
  entrypoints/
  release-manifest.json
  THIRD_PARTY_NOTICES.txt
  sbom.spdx.json
```

第一稳定版优先使用“私有 Python runtime + wheel”的透明 onedir。它比直接冻结所有模块更易诊断、验证 migration 和复现。对用户暴露的是 Host EXE，不要求用户安装 Python。

后续如需冻结 Python，可将 payload backend 替换为 PyInstaller onedir，但 EntryPoint/Config/ResourceLocator/Host 协议保持不变。是否冻结由启动、杀软误报、包体积和兼容基准决定，不能改变应用架构。

### 11.2 Installer

Windows 安装器使用 Inno Setup 或通过 ADR 批准的等价工具：

- 支持 current-user/all-users；
- 安装 Host、初始 version payload、配置模板；
- 可选择 DESKTOP/SERVER；
- SERVER 创建专用服务账户或要求选择既有账户；
- 写入最小 ACL；
- 可选配置 Windows Firewall 可信网段规则；
- 注册卸载器；
- 卸载默认保留 ProgramData/LocalAppData 用户数据；
- 生成安装日志；
- 安装器、Host、payload manifest 均签名。

### 11.3 Windows Service

- Host 直接实现 SCM ServiceMain/stop/preshutdown 语义。
- SCM recovery 仅重启 Host；Host 自己管理 API/Worker 重启预算。
- 服务账户对 Program Files 只读，对实例目录按最小权限读写。
- 不允许 LocalSystem 作为默认运行账户。
- 服务模式不调用任何桌面 UI。
- Windows 更新/重启前接收 preshutdown，进入 drain 并写清晰状态。

### 11.4 Desktop launcher

- 同一 Host 以 interactive 模式启动。
- API ready 后打开系统默认浏览器。
- 可提供托盘图标作为后续 UX，不是运行正确性的依赖。
- 关闭浏览器不停止 API/Worker；由 Host UI/命令明确停止。
- 不引入 Electron。未来若需要壳，使用可选 WebView2 thin shell，仍通过相同本地 HTTP API。

---

## 12. Linux 兼容实现

### 12.1 从 Windows 第一期即满足的约束

- Python core 中不出现无保护的 Windows-only import。
- 路径不使用盘符判断或反斜杠拼接。
- 项目/媒体数据库路径是相对路径。
- Secret、TTS、Picker、filesystem capability 均走 Port。
- 正确性不依赖 PowerShell。
- 生产启动不依赖 Windows PID/CIM/端口查询。
- 发布清单不假设 `.exe` 后缀。
- 所有 subprocess 参数为数组。
- 文件名策略同时满足 Windows 保留名、大小写不敏感和 Linux POSIX 约束。

### 12.2 Linux package

首期提供：

```text
LocalDramaStudio-1.0.0-linux-amd64.tar.zst
```

包含 Linux private Python runtime、相同 wheel、相同 web dist、相同 migrations、Linux Host。随后按需求增加 deb/rpm；包管理器只安装 Host/version payload/systemd unit，不接管用户项目数据。

### 12.3 systemd

- systemd 启动固定 Host，Host 再管理 API/Worker。
- Host 以前台模式运行，stdout/stderr 进入 journald。
- 支持 `Type=notify`/watchdog 时由 Host 发送 ready/watchdog。
- 使用 `StateDirectory`、`LogsDirectory` 或明确目录 ACL。
- 配置 `NoNewPrivileges`、`PrivateTmp` 等 hardening，但不能阻断 FFmpeg/模型运行所需路径。

### 12.4 Container

Linux container 是可选发行物，不是唯一方式：

- API/Worker 可以同镜像不同 role；
- data/projects/work/backups 必须挂持久 volume；
- SQLite volume 必须是本机可靠 volume；
- ComfyUI 可位于宿主或另一私网主机；
- updater 不在容器内自更新镜像，由容器平台更新；
- migration 仍使用同一 Maintenance entrypoint 和 recovery contract。

---

## 13. 网络与安全

### 13.1 网络 profile

| Profile | 默认绑定 | 鉴权 |
|---|---|---|
| DESKTOP/LOCAL_ONLY | 127.0.0.1 | 本机实例 token + origin/CSRF |
| SERVER/LAN_SERVICE | 配置地址 | 稳定版默认必须启用单实例管理员鉴权 |

`trusted_lan_unauthenticated=true` 只能是显式风险接受项，并在启动日志、健康页和 UI 持续显示警告。不能因配置 LAN_SERVICE 就静默开放无鉴权写接口。

### 13.2 Secret

- API key 只在提交请求内存、SecretStore 和实际 provider 请求中出现。
- 不写 config、数据库明文、Job snapshot、日志、命令行或 release evidence。
- audit 只记录 secret ref、source、changed_at、actor，不记录值。
- SecretStore 不可用时，相应 provider 状态为 BLOCKED，不回退明文文件。
- Linux/Windows 迁移不复制 secret；新机器需重新授权。

### 13.3 更新供应链

- installer、Host 和 update package 必须有发布签名。
- `release-manifest.json` 列出全部文件 size + SHA-256。
- Updater 先验证外层签名，再验证内部 manifest，再逐文件验证。
- 不允许从 UI 输入任意 update URL。
- stable/beta channel 使用独立签名元数据。
- 离线 `.ldsupdate` 与在线包使用相同格式和验证流程。

---

## 14. 发布清单与兼容合同

### 14.1 release manifest

```json
{
  "schema_version": 1,
  "product": "LocalDramaStudio",
  "version": "1.0.0",
  "build_id": "git-sha+ci-run",
  "channel": "stable",
  "platform": {"os": "windows", "arch": "amd64"},
  "host": {"protocol": 1, "minimum_version": "1.0.0"},
  "python": {"version": "3.12.x", "abi": "cp312"},
  "database": {
    "accepted_heads": ["0060_visual_lab_runtime_foundation"],
    "target_heads": ["0060_visual_lab_runtime_foundation"],
    "requires_backup": true
  },
  "config_schema": {"minimum": 1, "target": 1},
  "files": [
    {"path": "app/...", "size": 123, "sha256": "..."}
  ],
  "sbom": "sbom.spdx.json",
  "signature": {"algorithm": "...", "key_id": "...", "value": "..."}
}
```

### 14.2 兼容规则

- Host protocol 不兼容：拒绝应用内升级，要求新 installer。
- DB head 高于当前 release 可接受范围：拒绝启动，绝不尝试 downgrade。
- DB head 低于 target：只能由 Updater/Maintenance 升级。
- Worker protocol 不匹配：Worker 不 claim Job，API 可以进入维护只读状态。
- config schema 旧：先备份并迁移 config；新于当前：拒绝启动。
- 项目 package schema 独立于安装版本，继续走现有兼容合同。

---

## 15. 升级与回滚状态机

### 15.1 更新目录

```text
updates/
  sessions/<upgrade-id>/
    state.json
    package.ldsupdate
    extracted/
    logs.jsonl
    recovery-set-ref.json
```

`state.json` 每次转换原子写入，Host 重启后可继续或回滚，不依赖内存状态。

### 15.2 升级流程

```text
DISCOVERED
  → DOWNLOADING/IMPORTING
  → PACKAGE_VERIFIED
  → EXTRACTED
  → RELEASE_VERIFIED
  → PREFLIGHT_PASSED
  → DRAINING
  → STOPPED
  → RECOVERY_SET_COMPLETE
  → DB_REHEARSAL_PASSED
  → DB_UPGRADED
  → CANDIDATE_STARTED
  → CANDIDATE_VERIFIED
  → ACTIVE_POINTER_COMMITTED
  → COMPLETED
```

关键顺序：

1. 下载阶段不停止当前版本。
2. 验签和解压全部在 staging。
3. preflight 检查空间、平台、Host、DB/config 兼容。
4. drain 后停止进程，创建完整 Recovery Set。
5. 在数据库副本上执行相同 migration 并验证。
6. 正式 migration 后启动 candidate。
7. candidate 必须通过 API/Worker/static asset/DB/关键只读路径 smoke。
8. 成功后才切换 active pointer。
9. 旧 version 和 recovery set 保留到 retention 门槛。

### 15.3 自动回滚

以下任一失败触发回滚：

- migration 命令失败；
- migration head 不匹配；
- candidate readiness 超时；
- Worker protocol 不兼容；
- static asset 缺失；
- installed-artifact smoke 失败；
- Host 在 pointer commit 前崩溃并恢复时发现未完成状态。

回滚过程：

1. 停止 candidate。
2. 保存失败数据库、日志和 session，不覆盖证据。
3. 从 COMPLETE Recovery Set 恢复数据库/config/必要项目文件。
4. 验证 integrity、revision、hash。
5. 启动 previous release。
6. previous readiness 通过后标记 ROLLED_BACK。

禁止执行 Alembic downgrade 冒充回滚。

### 15.4 pointer commit 后故障

pointer commit 后首次运行观察窗口内出现 crash loop，Host 可执行自动回滚，但必须连同数据库恢复到升级前 Recovery Set。不能仅切换旧代码读取新 schema。

---

## 16. 可观测性与诊断

### 16.1 结构化日志

所有角色输出统一 JSON line：

- timestamp UTC；
- level；
- process_role；
- release/build；
- instance_id；
- request/trace/job/attempt/session id；
- event_code；
- redacted details。

Host、API、Worker、Maintenance 分文件/stream。Windows 写文件并可选 Event Log 摘要；Linux 写 stdout/journald 并可选文件。

### 16.2 diagnostics bundle

Maintenance 生成可分享诊断包，默认包含：

- redacted config；
- release/Host identity；
- migration heads；
- health/capabilities；
- 最近日志；
- storage/space/ACL 摘要；
- FFmpeg/Comfy/LLM 探测摘要；
- upgrade session 状态。

默认排除 API key、完整绝对模型路径、媒体内容、用户剧本和数据库正文。

### 16.3 操作命令

统一 CLI：

```text
LocalDramaStudio.Host status
LocalDramaStudio.Host start
LocalDramaStudio.Host stop --drain-timeout 300
LocalDramaStudio.Host restart
LocalDramaStudio.Host doctor
LocalDramaStudio.Host backup
LocalDramaStudio.Host restore <recovery-set>
LocalDramaStudio.Host update import <package>
LocalDramaStudio.Host update apply
LocalDramaStudio.Host rollback
```

Windows GUI/安装器只调用同一控制协议，不另写第二套升级逻辑。

---

## 17. 构建与发布流水线

### 17.1 可复现构建

- Python 依赖使用带 hash 的锁定文件或受控 wheelhouse；不再只依赖无 hash requirements 文本。
- pnpm 必须 `--frozen-lockfile`。
- 构建环境锁定 Python、Node、pnpm、Go 和安装器版本。
- Windows artifact 必须在 Windows runner 构建；Linux artifact 在 Linux runner 构建。
- Web dist 只构建一次逻辑版本，可按平台复用并验证 hash 一致。
- release manifest 在全部 payload 完成后生成，签名是最后一步。

### 17.2 Pipeline

```text
source checks
  → unit/integration tests
  → OpenAPI/client generation clean check
  → web production build
  → Python wheel build
  → Host build
  → platform payload assembly
  → SBOM/license scan
  → manifest/hash
  → signing
  → installer/package
  → clean VM install
  → installed artifact smoke
  → N-1/N-2 upgrade and rollback
  → publish candidate
```

### 17.3 发布工件

```text
LocalDramaStudio-Setup-1.0.0-windows-amd64.exe
LocalDramaStudio-1.0.0-windows-amd64-portable.zip
LocalDramaStudio-1.0.0-linux-amd64.tar.zst
LocalDramaStudio-1.0.0-linux-amd64.oci.tar     # 可选
LocalDramaStudio-1.0.0.ldsupdate
checksums.txt
sbom.spdx.json
release-notes.md
```

portable 包也必须使用外部数据目录，不能默认把生产数据库写回解压目录。

---

## 18. 测试矩阵

### 18.1 层级

| 层级 | 目标 |
|---|---|
| Unit | Platform Port、config、manifest、state machine、path policy |
| Contract | Windows/Linux adapter 对相同合同的行为 |
| Integration | API/Worker/SQLite/FFmpeg/Host IPC |
| Packaging | 从真实 payload 启动，不借用源码 `.venv`/node_modules |
| Upgrade | N-1/N-2、失败注入、断电恢复、pointer/recovery |
| UAT | Windows DESKTOP、Windows SERVER、Linux SERVER |

### 18.2 Windows 必测

- Windows 10/11 x64 桌面模式。
- Windows Server 支持版本的服务模式。
- 无 Python、Node、pnpm 的干净机器。
- 当前用户/all-users 安装。
- 中文用户名、空格路径、非 C: 数据盘、长路径。
- 普通用户权限、专用服务账户、ACL 拒绝。
- 文件选择器只在交互模式出现。
- Credential Manager 在桌面/服务身份分别持久化。
- SAPI 可用和不可用两种状态。
- 防火墙、端口占用、重启、服务恢复。
- 安装、升级、回滚、卸载保留数据。

### 18.3 Linux 必测

- 主目标发行版 x64，glibc 最低版本写入支持矩阵。
- systemd 服务用户与目录权限。
- 无 GUI、无 TTS、无 Secret Service 时核心服务仍启动。
- ext4/XFS；显式拒绝 NFS 数据库。
- FFmpeg system/bundled 两种解析。
- tar 安装、升级、回滚。
- container volume 与 signal/drain。

### 18.4 故障注入

- 下载中断/坏签名/坏 hash。
- 解压磁盘满。
- Recovery Set 写一半断电。
- migration 副本成功但正式 migration 失败。
- DB upgraded 后 candidate 启动失败。
- pointer 临时文件已写但未 replace。
- API/Worker crash loop。
- SQLite WAL 未 checkpoint。
- 项目文件被占用、杀毒软件锁文件。
- 备份盘离线。

### 18.5 性能回归

- 大项目 metadata benchmark。
- 1/10/50 GB 媒体上传、Range 播放、hash、代理生成。
- API 读并发 + Worker 写并发。
- WAL 增长与 checkpoint。
- Host/API/Worker RSS、句柄/FD 泄漏。
- 连续运行 72 小时 soak。

---

## 19. 实施工作包

### WP0：ADR 与基线冻结

交付：

- 接受本文关键决策的 ADR；
- 当前启动/升级/平台耦合测试基线；
- 工作区可恢复 commit/tag；
- 明确 Windows/Linux 支持矩阵。

不得改业务行为。

### WP1：统一版本、配置和 ResourceLocator

交付：

- `release/version.json`；
- `contracts/config.schema.json`；
- `bootstrap/config_loader.py`；
- `bootstrap/resource_locator.py`；
- 删除运行时对 `REPO_ROOT` 的数据/资源双重语义；
- config/env compatibility importer。

验收：源码、wheel、任意 cwd 和模拟 frozen root 得到一致路径。

### WP2：Python package 与正式 EntryPoints

交付：

- `src/` layout；
- `entrypoints/api.py`、`worker.py`、`maintenance.py`；
- 去除 `--app-dir`、`sys.path.insert` 生产依赖；
- wheel build/test。

验收：从已安装 wheel 运行完整 API/Worker 测试。

### WP3：Platform Ports 与 Windows adapter

交付：

- SecretStore、FilePicker、TtsRuntime、PlatformFilesystem；
- Windows desktop/server adapter；
- application 中 PowerShell/WinForms/Credential ctypes 调用迁出；
- capability API。

验收：Windows 原能力测试保持；server profile 不产生桌面 UI。

### WP4：Maintenance、数据库与 Recovery Set

交付：

- 结构化 maintenance CLI；
- DB inspect/backup/rehearse/upgrade/verify/restore；
- Recovery Set schema；
- maintenance lock；
- 项目清单与恢复演练。

验收：启动 API 不迁移；升级失败可精确恢复。

### WP5：Runtime Host

交付：

- Go Host；
- Host protocol/schema；
- Windows interactive + SCM；
- single-instance、process supervision、drain；
- Named Pipe control；
- 原子 active release。

验收：API/Worker 崩溃、重启、系统重启和 stop timeout 均有确定状态。

### WP6：Windows 自包含 payload 与安装器

交付：

- private Python runtime/wheel/web/migrations payload；
- frozen lock/wheelhouse；
- signed release manifest/SBOM；
- Inno Setup；
- clean VM installed-artifact tests。

验收：机器无开发环境也能安装、启动、生成 CPU 任务、停止和卸载。

### WP7：Updater 与版本回滚

交付：

- `.ldsupdate`；
- download/import、verify、staging；
- upgrade state machine；
- N-1/N-2 recovery；
- stable/beta/manual channel。

验收：全部故障注入不会留下“旧代码+新 DB”或“新代码+旧 DB”的不可判定状态。

### WP8：Linux adapters 与 systemd

交付：

- Linux filesystem/secret/unavailable picker/TTS adapter；
- Linux Host build；
- systemd units；
- tar package；
- Linux CI/installed-artifact/upgrade tests。

验收：相同 DB/project package 在重绑定外部资源后可于 Linux SERVER 运行。

### WP9：性能、安全与发布硬化

交付：

- streaming/backpressure/static cache；
- 性能基准和 soak；
- LAN 鉴权；
- 签名与密钥轮换；
- diagnostics bundle；
- 运维手册和最终 go/no-go。

---

## 20. 从当前仓库迁移

### 20.1 兼容窗口

重构期间允许旧开发脚本与新 Host 并存，但只有一个生产权威入口：

- `scripts/start_api.ps1`、`start_web.ps1` 保留为开发命令并移动到 `scripts/dev/`。
- `scripts/start.ps1`、`stop.ps1` 在 Host 完成后输出 deprecation，并调用 Host CLI；不再保留独立生命周期实现。
- `scripts/migrate.py` 逻辑迁入 Maintenance，旧脚本仅作为薄兼容入口。
- `doctor.ps1` 迁入跨平台 diagnostics，旧脚本不得继续硬编码 FFmpeg 路径。

兼容入口在两个稳定版本后删除，删除前必须有使用审计和迁移文档。

### 20.2 旧数据接管

首次 Host 启动检测 legacy repo 数据时，不自动搬移：

1. 只读扫描旧 data/projects/work/config。
2. 生成 import plan、空间估算和风险。
3. 用户选择“继续引用现有外部数据根”或“维护模式迁移”。
4. 迁移前建立 Recovery Set。
5. 迁移完成后验证 DB、项目清单和外部模型 rebind 状态。

大型项目默认继续引用现有 projects root，避免无意义复制；后续通过显式 relocate 命令搬迁。

### 20.3 DB migration

本次运行时重构原则上不要求重建业务数据库。只有以下新事实确需 migration：

- platform-neutral resource binding；
- install/update audit facts（业务审计副本，升级权威仍在外部 state）；
- config schema reference 或 capability evidence。

不得为了目录重构复制所有业务表或引入第二数据库。

---

## 21. Definition of Done

Windows 首发只有同时满足以下条件才可标记稳定：

1. 生产运行不依赖源码目录、系统 Python、Node、pnpm。
2. API/Worker/maintenance 从同一 release identity 构建。
3. Runtime Host 在 Windows DESKTOP/SERVER 均通过故障和重启测试。
4. 业务代码不直接依赖 Windows UI、Credential 或 SAPI 实现。
5. API 正常启动永不隐式迁移生产 DB。
6. 升级前存在 COMPLETE Recovery Set 和副本迁移演练。
7. candidate 失败能自动恢复旧代码+旧 DB+旧 config。
8. 安装、升级、回滚测试在无开发环境的干净 Windows VM 运行。
9. LAN 默认安全策略通过评审；无鉴权模式只能显式开启。
10. 发布包、manifest 和 installer 已签名并有 SBOM。
11. 性能、内存、大文件流式和 72 小时 soak 达标。
12. Linux CI 至少运行 core/platform-neutral tests，防止 Windows 首发期间重新引入平台耦合。

Linux SERVER 稳定版另需：

1. systemd 生命周期、signal、watchdog 和权限通过。
2. 无 GUI/SAPI/Secret Service 环境能以能力降级启动。
3. Linux payload/升级/回滚通过 installed-artifact tests。
4. Windows 项目包在 Linux 重绑定外部资源后可读、可生成、可导出。
5. ext4/XFS 支持并明确拒绝不安全的网络数据库文件系统。

---

## 22. 实施顺序与依赖

```text
WP0
 └─ WP1
     ├─ WP2
     │   ├─ WP3
     │   └─ WP4
     └─ contracts
          └─ WP5
              ├─ WP6
              │   └─ WP7
              └─ WP8

WP3 + WP4 + WP5 + WP6 + WP7 + WP8
  └─ WP9
```

严格规则：

- 在 WP1/WP2 完成前不编写安装器。
- 在 WP4 完成前不实现自动 updater。
- 在 WP5 完成前不再增强旧生产启动脚本。
- 在 Platform Ports contract tests 通过前不声称 Linux 兼容。
- 在 installed-artifact tests 通过前不以源码测试冒充安装包通过。

---

## 23. 评审时必须确认的少量产品决策

架构实施不依赖这些问题全部回答，但 Windows installer 定稿前必须确认：

1. Windows Server 最低支持版本。
2. 默认 DESKTOP 还是由安装向导选择 profile。
3. FFmpeg 是否随包分发及相应许可证/构建来源。
4. LAN 鉴权的首期形式：单管理员密码、一次性配对或外部反向代理集成。
5. 在线更新服务地址与签名密钥托管方式。
6. 默认备份盘、retention 和大项目 full/incremental 策略。
7. Linux 首个正式支持发行版和 glibc 基线。

这些选择只能改变 adapter、installer 或安全策略，不得改变本文定义的业务核心、Host 协议、版本目录和 Recovery Set 架构。

---

## 24. 最终设计判断

LocalDramaStudio 最合适的长期结构不是“一个越来越复杂的 Windows 脚本”，也不是为了跨平台重写成另一套服务，而是：

```text
稳定的模块化业务核心
+ 明确的 Platform Ports
+ 小型跨平台 Runtime Host
+ 自包含、只读、版本化 payload
+ 外置持久数据
+ Recovery Set 驱动的升级/回滚
+ Windows/Linux 分平台构建与共同合同测试
```

该结构优先保证数据安全、可恢复性和运行时确定性；性能通过单 API、durable Worker、流式媒体、短 SQLite 事务和同卷原子提交获得。Windows 可以先形成成熟安装体验，而不会把后续 Linux 服务版锁死在 PowerShell、盘符、SAPI 或 Windows Credential Manager 上。
