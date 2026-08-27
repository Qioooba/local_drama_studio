# 数据迁移、回滚与新安装基线（Slice 8）

> 权威来源：设计文档 §9.3 离线原子迁移流程、§10.3 Slice 8、§11.4/§11.5 验收口径。
> 应用升级/恢复的运行期操作见同目录 [upgrade-recovery-runbook.md](./upgrade-recovery-runbook.md)；
> 本文只覆盖**数据库迁移状态、离线迁移/回滚演练、全新安装基线**三件事，不重复 Runbook 命令。

## 1. 迁移链状态

- 当前 Alembic head：`0063_audio_mix_drafts`（`apps/api/alembic/versions/`）。
- 历史基点：v2 重构期间的关键迁移包括 `0058_one_sentence_video_runs`、`0059_automation_workflow_versions`、`0060_visual_lab_runtime_foundation`、`0061_shot_working_media_slots`（镜头工作媒体唯一真值）、`0062_canonical_job_scope_stage`（Job subject/stage/scope canonical 化）、`0063_audio_mix_drafts`。
- 禁止重号迁移；新迁移必须从当前 head 顺序追加。

## 2. 离线迁移与等价性验证（升级前在副本上执行）

1. **备份**：SQLite 在线备份走 maintenance 的 online backup 路径（Runbook "Manual maintenance"），禁止直接拷贝活动库文件。
2. **副本演练**：把备份复制到隔离目录后执行 `alembic upgrade head`；Host 升级流程的第 7 步会自动做同样的 copy-rehearse。
3. **等价性与不变量检查**（§11.4 口径）：
   - `PRAGMA foreign_key_check;` 必须为空；
   - 迁移前后行数对比：revision/variant/decision/job/attempt/artifact 只增不清；
   - 无法确定归属的数据进入可见 quarantine，不静默丢弃；
   - 旧库 current selected/approved head 作为 cutover 初始权威保留。
4. **失败处理**：任何一步校验失败即终止本次升级；live 库保持原状，由 Host 自动 restore 副本演练前的恢复集。

## 3. 回滚边界

- **永远不用 Alembic downgrade 代替恢复**。回滚 = 恢复与目标应用版本匹配的 pre-upgrade 恢复集（Runbook "Roll back" 的完整流程）。
- 恢复集按保留窗口留存；在双份 COMPLETE 恢复集存在前不得清理旧版本。
- 开发阶段的源代码级回滚锚点用 git tag：`refactor-baseline-20260827` → `refactor-step1-worker-handlers-20260827` → `refactor-step2-worker-handlers-complete-20260827` → `refactor-step3-web-legacy-purge-20260827`（后续 tag 依提交追加）。

## 4. 全新安装基线

### 4.1 分发包安装（生产）

- Linux：打包产物内 `install.sh` 以 root 执行（创建 systemd 服务、实例目录并调用 Host `upgrade --bundle payload` 完成首迁）；Windows 走 Inno Setup 安装器与 Host 同流程。首迁即"空库 → head"，不需要单独跑 Alembic。

### 4.2 开发仓冷启动

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r apps\api\requirements.lock -r apps\api\requirements-runtime.lock
$env:PYTHONPATH = 'apps/api'
.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head   # 仓库根执行；head=0063_audio_mix_drafts
scripts\dev\start_api.ps1          # 默认 http://127.0.0.1:3210
pnpm -C apps\web install; pnpm -C apps\web run dev
```

生产/实例环境的等价动作为 `local_drama-maintenance upgrade|rehearse-upgrade|restore ...`（见 Runbook "Manual maintenance"）；Host 升级在安装包内自动完成同样的首迁。

### 4.3 新装验收清单

| 检查 | 期望 |
|---|---|
| `GET /api/v1/health` | 200 |
| 登录 `/projects` | AppContext 单次聚合加载，无静态第二导航 |
| 公开 OpenAPI（`docs/openapi/openapi.json` 快照契约测试） | 当前 405 条路径；`/gates/g*`、`:claim/:heartbeat/:complete`、`/comfy*` 直控均**不在**公开 schema 且功能可用 |
| 旧 URL 抽查 | `/models`→`/system/capabilities`、`/jobs`→`/system/jobs`、`/diagnostics`→`/system/diagnostics`、`/lab`→`/system/workflows`、`/episodes/:eid/run`→production、`direct/generation`→studio（保留 shot 精度与 query/hash） |
| Worker 无任务轮询 | `run_until_idle` 直接返回，UI 无固定周期全局轮询 |

## 5. 兼容 redirect 的退役窗口

旧路由 redirect 属**首个发布版本**的新增兼容面：两稳定版本的观察窗从首次公开发布起算；发布说明需列出 §5.4 切换表中全部 redirect。窗口期满后按 Slice 8 规则删除对应路由行与 `LegacyRedirect` 组件分支。

## 6. 已知架构债务边界

过渡期债务以机器可读清单为准：`docs/architecture/legacy-debt-manifest.json`
（policy：allow-list，禁止新增条目；守卫测试 `test_architecture_debt_manifest.py`）。剩余主要类别为 application 层对具体 Database 与 service 内部构造的存量记录，按各 slice 归属逐模块清零，Slice 8 收敛到 0（§11.3）。
