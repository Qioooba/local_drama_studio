# LocalDramaStudio 安装 / 升级 / 回滚（FINAL）

release_status: FINAL

本手册适用于 Windows x64 本地源码发行版。平台只包含应用代码和锁定依赖；用户选择的模型、音色和媒体始终保留在原电脑路径，不进入安装包。

## 安装

1. 在受控本地工作区创建 Python `.venv`，使用 `apps/api/requirements.lock` 安装依赖。
2. 使用仓库锁定的 `pnpm-lock.yaml` 安装 Web 依赖并运行生产构建。
3. 运行 `scripts/start.ps1`。脚本必须先执行 `alembic upgrade head`，成功后才绑定 `127.0.0.1:3210`。
4. 只允许通过 `http://127.0.0.1:5173` / `http://127.0.0.1:3210` 验收；REMOTE transport 保持禁用。

## 升级

1. 停止由 `runtime/api.pid.json` 追踪的 API 进程，不停止用户未授权的 ComfyUI 工作。
2. 将 `data/local_drama.sqlite3` 复制到受控 `backups/`，记录 SHA-256 与 `PRAGMA integrity_check`。
3. 更新代码与锁文件，在离线环境运行 `scripts/migrate.py --database data/local_drama.sqlite3`。
4. 运行 `scripts/check.ps1`、本地 UAT 和 `scripts/release_audit.py`；只有审计明确 PASS 才能进入正式评审。

## 回滚

1. 停止 LocalDramaStudio API/Worker，保留当前数据库与审计日志。
2. 从指定备份复制回 `data/local_drama.sqlite3`，再次执行 `PRAGMA integrity_check`，不得删除历史备份。
3. 检出与备份对应的代码 commit，重新运行 `scripts/start.ps1`；不得对数据库执行未经评审的降级 migration。
4. 验证 migration head=`0039_automation_task_jobs`、G7/G8/G9 readiness、API/Web 回归和 LOCAL_ONLY 连接状态。

## 支持边界

- 正式交付形态是 Windows x64 本地源码发行版，不承诺单文件 EXE 或内置模型。
- Python/PNPM 依赖由锁文件安装；离线环境需预先准备对应缓存。
- 用户模型通过页面选择本机绝对路径，平台只保存引用和 hash，不复制、上传或卸载模型。
- 回滚采用数据库备份加匹配代码版本，不执行破坏性的 downgrade migration。

## 已完成的隔离演练（2026-08-15）

已将 `backups/pre_migration_20260814T193026Z.sqlite3` 复制到受控临时目录，执行
`0020_g7_model_license_evidence → 0021_g10_scale_read_indexes` 的 Alembic
升级，并对另一份独立副本执行恢复校验。升级副本与恢复副本均通过
`PRAGMA integrity_check`；恢复副本 SHA-256 与源备份一致。正式生产库、API、ComfyUI、
网络和任务队列均未接触，证据见
`docs/evidence/g10/upgrade-rollback-rehearsal-2026-08-15.json`。

最新演练使用 `0028_audio_binding_authority` 生产前备份，在隔离副本升级到
`0029_user_supplied_model_policy`，并验证另一独立恢复副本与源备份 SHA-256 完全一致。
生产数据库未被演练触碰，证据文件记录为 PASS。

版本化视频增强链迁移另使用 `0029_user_supplied_model_policy` 生产前备份，在隔离副本
升级到 `0039_automation_task_jobs`，同时验证独立恢复副本与源备份 SHA-256
完全一致；证据见 `docs/evidence/g10/upgrade-rollback-rehearsal-0039-2026-08-16.json`。

## 离线 SBOM 盘点（2026-08-14）

`scripts/generate_sbom.py` 已在无网络条件下读取两个锁文件和本地包元数据，生成
`docs/release/sbom.json`，共 317 个锁定包条目并保留 lockfile SHA-256。当前 73 个
条目没有可由本地元数据确认的许可证；生成器已根据 pnpm lockfile 的 `os`/`cpu` 约束逐项证明它们全部是与 Windows x64 发布目标不兼容、未安装的跨平台可选包，目标运行时 `NOASSERTION=0`。锁文件完整清单仍保留这些条目并使用 SPDX `NOASSERTION`；因此 SBOM 和本文件
目标 Windows x64 运行时 `NOASSERTION=0`；其余 73 项均为锁文件中的非目标平台可选包。

## 本地只读 UAT 基线（2026-08-14）

`scripts/local_uat_readonly.py` 已对健康状态、系统契约、adapter 声明、容量快照、
G7/G8/G9 readiness 和模型兼容性执行 8 个 GET 请求；8/8 成功，6 项安全断言通过，
未接触 runtime、网络或任务队列。证据见
`docs/evidence/g10/local-uat-readonly-2026-08-14.json`。该基线不替代完整本地一条龙
UAT、真实生成、性能/可访问性验收或最终发布评审。

## 隔离元数据规模 UAT（2026-08-15）

`scripts/g10_scale_uat.py` 在独立迁移库建立 60 集、800 镜头、10,000 MediaAsset 与
10,000 MediaVersion，并通过真实 FastAPI read path 验证 60/60 集的生产入口和
timeline/delivery 状态入口。生产 read-model p95 为 11.881ms，timeline/delivery p95
为 14.461ms，review inbox 为 156.986ms，数据库完整性为 `ok`。这些媒体行的
`integrity_status` 固定为 `UNKNOWN`，不冒充可播放媒体或媒体回归证据。详见
`docs/evidence/g10/metadata-scale-uat-2026-08-15.json`。

## 干净新根恢复 UAT（2026-08-15）

`scripts/recovery_restore_uat.py` 在隔离根生成并用真实 FFprobe 验证 100 个本地 WAV，
执行 SQLite online backup 后把数据库和项目树恢复到新的干净根。恢复库 integrity=ok，
100/100 MediaVersion 文件 SHA-256 匹配，健康、项目和 100 条审核入口均可读；实测
RTO 0.627 秒，RPO 为捕获备份后的零记录丢失。证据见
`docs/evidence/g10/recovery-restore-uat-2026-08-15.json`。该隔离演练不替代最终安装包
该恢复 UAT 与升级/精确回滚、完整回归、LOCAL_ONLY 安全 UAT 和锁文件 SBOM 共同构成本地源码发行版的正式安装运维证据。
