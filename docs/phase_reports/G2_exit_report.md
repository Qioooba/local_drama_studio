# G2 阶段退出报告

- 基线：G1 骨架；Alembic schema `0001_g2_core`
- 阶段结论：PASS
- 继续方向：进入 G3（Profile/媒体/导入/读模型/诊断）

## 已交付

- SQLite WAL 数据库、外键、`busy_timeout`、事务封装、`integrity_check`。
- 真实 Alembic 初始迁移，覆盖项目、季、集、镜头、修订、媒体、审核、模型/Profile、任务/Attempt/Artifact、变体/绑定/实验/首尾帧/转场、时间线、交付、审计、outbox 和幂等表，共 42 张表。
- 项目创建事务：固定目录模板、60 集初始化、`project.json`、失败清理和 DB/文件系统一致性边界。
- Project/Season/Episode/Shot API 与服务：列表、读取、标题更新、状态转换、集排序、镜头创建、修订和 production-ready 阻塞检查。
- 领域规则：显式规格、审核门禁、交付来源、LOCAL_ONLY 传输、变体 lineage/binding/seed/replay 约束。
- 在线 SQLite backup 与迁移前置备份。
- OpenAPI snapshot 与 TypeScript client 已按 G2 路由重新生成。

## 门禁证据

详见 `docs/evidence/g2/g2_validation.txt`。

- API/领域/服务/迁移测试：15 passed，0 failed。
- Ruff：通过。
- mypy strict：25 个 Python 源文件通过。
- 数据库：`0001_g2_core`、42 tables、WAL、integrity ok；应用连接启用 foreign keys。
- 从 `apps/api` 工作目录调用 migration：通过，证明脚本路径不依赖当前目录。
- G0 计数复核：86 FR、14 NFR、85 TC。
- 测试使用真实 SQLite/Alembic/API；未用 mock 数据或静态业务页面冒充完成。

## 追踪与边界

- 已验证：项目模板/身份与排序/修订并发保护/镜头 readiness/审计出站基础、生成变体基础 schema 与领域约束。
- 尚未声称：Profile 真值注册、媒体导入与 probe/hash、持久任务队列、ComfyUI/本地模型真实生成、审核 UI、音视频交付、全量 85 TC、UAT 和正式发布。
- `Idempotency-Key` 的统一持久化语义由 G5 任务/命令幂等工作包闭环；G2 API 已保留 header 接口但不把其声明为完成。
- G11 旧项目迁移仍然延期且未实施；云 Provider/API Key/计费/多租户仍未实施。

## 回滚

G2 迁移提供迁移前置在线备份；降级不执行破坏性反向 DDL，按备份恢复并重新运行 integrity check。当前工作区仅新增本地开发产物，未修改蓝图目录和既有旧项目。

## 门禁结论

`PASS — G2 database/domain/template/API baseline is green; G3 may start.`
