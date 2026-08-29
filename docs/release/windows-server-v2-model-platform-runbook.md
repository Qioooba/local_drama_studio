# Windows Server V2 模型平台发布与验收手册

本手册实施《模型平台重构与 Windows Server 部署设计》中的机器部署边界。它适用于 Windows Server 上的 Local Drama Studio Runtime Host、API、Worker 和受管模型目录。

## 1. 目录与所有权

| 范围 | 默认位置 | 所有者 | 可变性 |
|---|---|---|---|
| ReleaseRoot | `%ProgramFiles%\LocalDramaStudio\versions\<version>` | 安装器/Host | 不可变 |
| InstanceRoot | `%ProgramData%\LocalDramaStudio` | 服务实例 | 可变、升级保留 |
| ModelRoot | `%ProgramData%\LocalDramaStudio\models` | 服务实例 | 可变、可迁移至本地数据盘 |

ModelRoot 的规范子目录：

```text
models/
  downloads/                 # 下载任务的受控落点，不参与扫描
  staging/                   # 安装验证前的受控暂存，不参与扫描
  quarantine/                # 失败/隔离资源，不参与扫描
  libraries/
    comfyui/                 # 可发现的 ComfyUI 模型库
    pytorch/                 # 可发现的 PyTorch 模型库
    ollama/                  # Host 管理 Ollama 时的受管模型位置
    audio/                   # 可发现的音频模型库
```

模型身份始终是 `ModelLibrary + relative_path + fingerprint`，不是磁盘绝对路径。浏览器、项目数据库和 V2 Profile 不接收服务器路径。目录改变后必须重新发现/验证；不得手工把路径写进 Profile 或业务请求。

`model-lock` 是受控的 ComfyUI/PyTorch 组件清单，不是四类库的通用文件扫描器。默认分库中，历史清单前缀 `ComfyUI/...` 映射到 `libraries/comfyui/...`，`Services/...` 映射到 `libraries/pytorch/...`；发现和完整性验证必须使用同一映射，记录的仅是库内相对路径。`libraries/ollama` 由 Ollama API 观察，`libraries/audio` 由对应音频 Runtime Adapter 观察，二者绝不能因为 model-lock 缺项而产生“模型缺失”记录。旧的管理员自定义聚合库仍可读，但迁移不会移动或复制内容。

## 2. 首次安装

1. 以管理员身份运行已签名安装器，选择 Desktop 或 Trusted LAN Server。
2. 安装器创建 InstanceRoot 与默认 ModelRoot 的规范目录；它不下载、复制、扫描或自动发布模型。
3. Server 模式只在受信任内网使用。确认防火墙范围、服务账户和 `trusted_lan_unauthenticated=true` 已获组织许可。在线模型下载默认关闭；仅可由 Host 管理的 `runtime.model_download_source_hosts` 明确登记可信 HTTPS 主机。
4. 使用 `local-drama-host.exe doctor` 检查 Host、release、Python、配置及服务状态。
5. 从“模型中心”以服务身份扫描 Ollama 或受控 model-lock；发现记录只能登记候选，不能直接在创作页选择。

## 2.1 离线模型导入计划

对于隔离的 Windows Server，先在模型中心创建“离线导入计划”。计划必须选择已由 V2 登记、且仍属于当前服务配置的目标模型库，并声明发布标识、离线包标识、许可证标识以及每个组件的库内相对路径、SHA-256 和预期字节数。创建计划只写入审计合同：不会读取、复制、移动或下载文件，页面也不会收到离线包标识和服务器路径。

当前计划状态为 `AWAITING_OFFLINE_IMPORT`。停止 Host 后，服务账户将离线包**目录内容**放入 `<ModelRoot>\staging\<离线包标识>`；该目录必须只含计划中声明的文件，路径、SHA-256 与字节数均须完全匹配。随后以服务账户执行：

```powershell
& $releaseRoot\runtime\python.exe -m local_drama.entrypoints.maintenance offline-model-import `
  <install-plan-id> --confirm-host-stopped
```

该命令不会接受任意源路径：它只从对应 staging 目录读取，写入已登记的目标 Library 前后均验证哈希，拒绝覆盖同名文件，并把 Job、进度和经脱敏的失败代码写入 V2 安装审计。清单不匹配、哈希不匹配或复制后完整性不匹配时，整个 staging 包会原子转入 `quarantine`，计划标为 `QUARANTINED`；成功后才标为 `IMPORTED`，且仍须重新发现、完整性验证、能力 smoke 与 Profile 发布。浏览器不显示离线包标识或服务器路径。

## 2.2 可信 HTTPS 下载计划

仅当 `runtime.model_download_source_hosts` 已登记一个或多个可信主机时，模型中心才允许创建“可信下载计划”。页面需提交发布标识、目标 Library、许可证、包标识，以及每个组件的 HTTPS URL、库内相对路径、SHA-256 与字节数；API 只验证 URL 是否为精确 allowlist 主机并持久化计划，**不会联网**。创建响应及计划列表绝不回显 URL、包标识或服务器路径。

停止 Host 后，以服务账户执行：

```powershell
& $releaseRoot\runtime\python.exe -m local_drama.entrypoints.maintenance trusted-model-download `
  <install-plan-id> --confirm-host-stopped
```

计划从 `AWAITING_TRUSTED_DOWNLOAD` 进入 `DOWNLOADING`，每个组件下载到 `downloads/<包标识>` 后按 SHA-256 与字节数验证；全部通过才原子移动整个包到 staging，并变为 `AWAITING_OFFLINE_IMPORT`。下载区永不参与扫描，也不能直接导入。随后运行上述 `offline-model-import`。下载器拒绝重定向、未登记主机、覆盖已有文件、未声明大小或哈希不匹配；失败计划标为 `DOWNLOAD_FAILED`，审计仅记录脱敏错误代码，不会将已有下载文件写入模型库。

## 3. 使用独立数据盘

只允许本地卷作为 ModelRoot，拒绝 UNC 路径。先停止 Host，确认状态为 `STOPPED`，再执行：

```powershell
$host = "C:\Program Files\LocalDramaStudio\host\local-drama-host.exe"
& $host stop
# 等待：& $host status 的 status 为 STOPPED
& $host configure-model-root --path "E:\LocalDramaModels"
```

此命令：

- 只接受 schema v3 配置；
- 原子备份 `%ProgramData%\LocalDramaStudio\backups\config`；
- 自动创建 ModelRoot 规范目录；
- 保留管理员已经定义的 `runtime.model_library_roots`；
- 不移动、不删除、不复制、不自动登记模型。

数据搬迁必须由管理员单独完成。旧库可以暂时继续保留为自定义 `model_library_roots`；确认文件完整、ACL 正确后再移除旧库。迁移后按“发现 → 完整性验证 → 能力 smoke → Profile smoke → 发布”重新完成 V2 证据链。

## 4. 升级与回滚

1. 安装器停止 Host，验证候选 release；候选 release 先创建 config/database recovery point。
2. config v1 自动迁移到 v3：新增 `runtime.model_root`、四个 scanner library 默认值和默认关闭的可信下载主机策略。迁移不触碰模型文件。
3. 数据库与配置维护成功后，Host 才激活 release 并启动 API/Worker。
4. 任一维护失败时，候选 release 不激活；Host 使用 recovery set 恢复 config/database。ModelRoot 永不被 rollback 删除。
5. `rollback` 只回滚 release/config/database 合同；若管理员已经移动模型文件，必须保证旧 release 所需 library binding 仍可见或先恢复相应的受管库。

## 5. 服务身份与 ACL

- 服务账户对 InstanceRoot 和 ModelRoot 具备所需的读写执行权限；浏览器用户不需要、也不应拥有服务器路径访问权。
- ComfyUI、PyTorch 子进程和 Host 管理的 Ollama 以明确服务身份运行。Ollama 的 `OLLAMA_MODELS` 必须指向受管位置，不能假定交互用户的模型目录可见。
- 在该身份下验证 GPU、模型根、下载/暂存/隔离目录、Credential Manager 和所需运行时。不要依赖用户桌面、用户 APPDATA 或开发 venv。

## 6. 发布验收清单

在启动 API/Worker 前，使用与服务账户相同的身份执行离线结构门禁：

```powershell
& $releaseRoot\runtime\python.exe $releaseRoot\app\model_platform_release_gate.py `
  --config "$env:ProgramData\LocalDramaStudio\config\config.json" `
  --release-root $releaseRoot `
  --instance-root "$env:ProgramData\LocalDramaStudio" `
  --database "$env:ProgramData\LocalDramaStudio\data\local_drama.sqlite3"
```

安装包中的门禁会读取同一不可变 release payload 内的 `migration-contract.json`；它不会从 InstanceRoot、浏览器请求或开发源码树读取 migration authority。这样在 Windows Server 上运行的规则与该 release 实际携带的数据库目标保持一致。

只有 JSON 输出 `status=PASS` 才进入运行时 smoke。该门禁不扫描模型文件、不联络 Ollama/ComfyUI、不创建 Job；它验证 schema v3 配置、非 UNC/non-reparse ModelRoot、四个可发现库与操作区隔离、SQLite integrity、release migration head、Project Knowledge V2 表结构，以及每个已发布 V2 Profile 是否都有当前 release 声明的 capability/adapter Worker handler。最后一项仅比对数据库合同和代码注册表，不泄露 adapter 细节，也不把“已发布”误判为“可执行”。

| 门禁 | 验收证据 | 通过标准 |
|---|---|---|
| 配置 | `doctor`、config migration backup | schema v3，ModelRoot、四类可发现库与可信下载主机策略已配置 |
| 目录安全 | 服务身份下目录创建/读写、reparse 检查 | 数据根可用；下载/暂存/隔离不进入 scanner roots |
| 运行时发现 | 模型中心 discovery evidence | Ollama / model-lock 仅报告当前服务身份可见资源，未泄露路径 |
| 安装完整性 | V2 integrity validation | ComfyUI/PyTorch 受控组件、哈希和清单一致 |
| 离线导入 | `offline-model-import --confirm-host-stopped` 的 JSON Job 结果 | 只消费对应 staging 包；逐文件哈希/大小复核；失败进入 quarantine；成功后仍重新走发现与验证链 |
| 可信下载 | `trusted-model-download --confirm-host-stopped` 的 JSON Job 结果 | 只执行已持久化且 allowlist 通过的 HTTPS 计划；逐文件哈希/大小复核；成功只进入 staging，随后仍需离线导入；失败不写 Library |
| 验证审计 | 候选模型的 validation history | 管理员可核对完整性/能力 smoke 的结果和时间；Web 响应不含原始 evidence、路径、endpoint 或密钥 |
| 能力可用性 | capability smoke | 每个能力有真实 Adapter/运行时证据；失败保持 fail-closed |
| Profile | profile smoke + publication audit | 只有已发布 Profile 可分配给范围 |
| SYSTEM V2 Assignment | 模型中心 Assignment 目录、保存审计与 override version | 仅能选择已发布 V2 Profile；仅显示安全且 SYSTEM 允许的参数；参数修改有理由/操作人并产生不可变版本；项目/集/镜头/角色范围必须经中心 ownership 校验；旧创作业务仍保持 V1，除非通过 Facade rollout 门禁 |
| 业务迁移 | 双读对账 + 已批准 crosswalk | capability、发布状态、参数字段/约束/有效默认值完全一致；不暴露参数值 |
| API/Worker | 任务与 artifact 证据 | Worker 仅消费冻结 snapshot，具备取消、超时、GPU lease 与重试语义 |
| 项目知识索引 | `0084`/`0085` migration、V2 IndexRun/Batch/Vector、Job/artifact lineage | 每个向量来自 `SUCCEEDED` V2 run；失败只能创建新 attempt，不能重置或覆盖既有向量；不得混读 legacy `embedding_indexes` / `embedding_chunks` |
| 项目知识检索 | `/api/v2/model-platform/project-knowledge-search` 的服务身份 UAT | 查询只提交文本；请求获得 `PYTORCH` GPU lease；只命中同一已发布 V2 Profile 下的已验证向量；响应不含路径、原文全文或向量 |
| Quick Create V2 单次文生图 | `/quick-create-v2/direct-image:preview`、`:submit` 与 `/jobs/{job_id}` 的服务身份 UAT | 仅在已发布的 `IMAGE_CONCEPT` Comfy Profile 具备单一必需 `PROMPT` 槽位和 IMAGE 输出合同后可提交；预检 hash 必须在提交时复核；只创建 V2 snapshot/Job/link；状态读取只投影该 command 的 SYSTEM-scope V2 Job、脱敏错误和已验证制品下载 URL，不能返回路径或 V1 run；所有响应 `legacy_quick_generation_touched=false`，V1 quick run 与默认按钮不受影响 |
| 网络 | firewall + remote-browser UAT | 仅配置端口和可信网段开放；远程浏览器不能读取/提交服务器路径 |

未实现真实 Handler、smoke 或服务身份验收的模型，保持“候选/不可执行”。绝不能因为扫描到文件、Ollama 列出 tag，或页面中出现模型名就把它发布为生产可用。
