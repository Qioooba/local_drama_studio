# 生产会话 24 小时长跑验收

`scripts/production_session_soak.py` 为一个已经启动的持久生产会话记录真实墙钟证据。它读取当前 session、分集 item、关联 Job、磁盘余量和 VERIFIED artifact，并将证据原子写入一个 JSON 文件。记录器被终止后，使用同一命令和同一输出文件重新启动，会延续原始 `started_at` 并增加 `restart_count`。

## 前提

1. 当前实例数据库已经升级到发布 migration head。
2. API、WorkerSupervisor、CPU/GPU worker 和本地模型 runtime 按正常产品方式运行。
3. 从“一键漫剧工厂”创建并启动隔离测试项目的生产会话，记下 session ID。
4. 测试项目应有真实可执行素材和明确预算；不要把用户正在制作的项目用于 kill/restart 演练。

记录器默认只观察，不启动会话，也不代替 WorkerSupervisor 投放任务。

## 真实 24 小时命令

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe scripts\production_session_soak.py `
  --session-id <PRODUCTION_SESSION_ID> `
  --output work\evidence\production-session-soak-<PRODUCTION_SESSION_ID>.json `
  --duration-hours 24 `
  --sample-interval-seconds 60 `
  --max-samples 2000
```

有限任务提前完成后，记录器仍会观察到 24 小时结束。合法空闲不会被伪造为持续生成；样本会保留 session 的真实终态、Job 数和产物数。

若记录器自身被停止，原样重跑命令即可续记。输出文件中的 `invocations` 会保留每次进程启动/结束时间，`restart_count` 会增加。输出文件已存在但 session ID 或申请时长不同，命令会拒绝覆盖。

证据同时记录 `elapsed_wall_clock_seconds` 与 `observed_duration_seconds`。真实 24 小时判定使用后者：正常相邻采样按实际间隔累计，超过两个采样周期的断档只计两个周期的容差。因此记录器或机器长时间停止后可以续跑，但停机空白不会自动算入 24 小时；记录器会继续运行，直到有效观察时长累计满 86,400 秒。

## 受控恢复演练

在隔离测试项目中执行一次：

1. 保持记录器运行，使用正常 Host/worker 停止命令结束服务，不强杀模型文件或数据库进程。
2. 记录停止时间，等待一个采样周期。
3. 重新启动 Host、worker 和本地 runtime。
4. 确认同一 session 继续推进，已有候选和 Job 没有重复创建。
5. 如需同时验证记录器恢复，停止记录器，再用完全相同的命令续跑。

`--drive-reconcile` 会让记录器额外调用幂等 session reconciler。正常产品运行由 WorkerSupervisor 负责，不需要该参数；仅在隔离恢复演练且 supervisor 明确未运行时使用，因为它可能继续投放真实任务。

## 证据判读

- `PASS_REAL_24H`：申请时长和有效采样覆盖都至少为 86,400 秒，观察到会话至少一次离开 `READY`、至少一个关联 Job，并且最终至少有一个关联 `VERIFIED` artifact；数据库完整性为 `ok`，所有关联 artifact 文件仍存在且 SHA-256 与登记值一致。空闲会话观察满一天不会通过，记录器关闭一天后重开也不会把断档算成有效覆盖。
- `PASS_SHORT_REHEARSAL`：短时运行通过，但 `real_24h=false`，不能用于 24 小时可靠性声明。
- `IN_PROGRESS`：记录器被中断或申请时长尚未达到，可用同一命令续跑。
- `FAILED`：数据库完整性、墙钟时长或 artifact 文件校验失败。

每个 sample 包含 session 状态/阶段/revision、分集状态和阶段分布、关联 Job 状态、VERIFIED artifact 数量及 work 盘剩余空间。样本由 `max_samples` 限制，避免长跑证据无限增长；最终 artifact 清单逐文件重新计算 SHA-256。

`observed_totals` 独立保存观察期内的最大关联 Job 数、最大已验证产物数和是否曾离开 `READY`，即使早期 sample 因 `max_samples` 被裁掉，也不会丢失是否发生过真实生产活动的事实。

真实验收还应附上本地模型/Profile/Workflow 版本、机器硬件信息、受控重启时间点、真实媒体 sample 和人工观察到的问题。该 JSON 不替代成片内容审核，也不会创建任何人工批准事实。

## 隔离双集整部验收

双集验收使用独立 instance root，避免把验证分集、镜头和任务写进用户项目。准备命令会在线备份当前数据库并复制指定项目目录；来源项目必须已经有一个 production-ready 镜头、有效 canonical 故事资产，以及镜头级明确的 `VIDEO_I2V` Profile：

```powershell
$UatRoot = ".codex-tmp\whole-drama-real-uat-$(Get-Date -Format yyyyMMdd-HHmmss)"
.\.venv\Scripts\python.exe scripts\prepare_production_session_whole_drama_uat.py `
  --instance-root $UatRoot `
  --project-id <SOURCE_PROJECT_ID>
```

随后给隔离 API/worker 同时设置 `LOCAL_DRAMA_INSTANCE_ROOT`、`LOCAL_DRAMA_COMFY_INPUT_ROOT` 和 `LOCAL_DRAMA_COMFY_OUTPUT_ROOT`。后两个路径必须指向当前 ComfyUI 实际共享目录；遗漏它们会使 provider 已成功但 worker 无法导入输出。启动准备文件中记录的 session 后，等待两个 item 都进入 `WAITING_REVIEW`，再采集证据：

```powershell
.\.venv\Scripts\python.exe scripts\capture_production_session_uat_evidence.py `
  --instance-root $UatRoot `
  --session-id <SESSION_ID> `
  --output docs\evidence\g10\production-session-real-whole-drama-uat.json
```

`PASS_REAL_WHOLE_DRAMA_TO_REVIEW` 要求所有分集均可人工审核、会话期间创建的 Job 没有失败或搁置、选择媒体和任务 artifact 的文件哈希全部匹配、Comfy prompt history 成功，并且自动化没有写入人工决策。验证允许复用已存在且完整性为 `VERIFIED` 的候选，但证据必须通过媒体创建时间和来源 attempt 区分“本会话新生成”与“已验证复用”。

若 worker 在 provider 成功后退出，启动恢复会先把不确定任务置为待处理，再查询 provider history。上游恢复为 `SUCCEEDED` 后，只有 `last_error_code=JOB_DEPENDENCY_FAILED` 且全部依赖现已成功的下游 Job 才会重新排队；其他人工处理状态不会被自动解除。
