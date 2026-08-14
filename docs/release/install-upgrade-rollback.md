# LocalDramaStudio 安装 / 升级 / 回滚（DRAFT）

release_status: DRAFT

这份文档是 G10 发布准备草稿，不是已验证的发布手册。当前 go/no-go 仍为 NO-GO。

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
4. 验证 G7/G8/G9 readiness、API/Web 回归和 LOCAL_ONLY 连接状态。

## 尚未实测

- 全新机器离线安装演练。
- 升级后完整 001x→head 恢复矩阵。
- 回滚后完整本地 UAT、SBOM 和 go/no-go 签字。

在上述演练完成并留存证据前，不得把本文件的草稿状态改为 `FINAL`。
