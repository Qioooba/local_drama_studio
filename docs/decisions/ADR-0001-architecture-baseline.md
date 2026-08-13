# ADR-0001：LocalDramaStudio 总体架构基线

- 状态：Accepted for G0 implementation baseline
- 日期：2026-08-12
- 需求：NFR-SEC-001、NFR-PRIV-001、NFR-REL-001、NFR-REL-002、FR-JOB-001、FR-PRV-002、FR-WFL-004

## 背景

系统需要在 Windows 单机上承载可恢复的 AI 短剧生产，同时保持媒体版本、人工审核和任务状态可追溯。当前工作区没有可继承的 LocalDramaStudio 应用代码，因此不能把旧脚本或旧项目目录当作核心架构。

## 决策

1. 采用 React + TypeScript 前端、FastAPI + Python Worker 后端、SQLite WAL、项目文件系统媒体存储。
2. 按 `domain → application → ports → infrastructure/api` 分层；route 和 Worker 不得直接绕过领域命令修改状态。
3. API、Scheduler/Reconciler、Worker、Designer ComfyUI、Production ComfyUI 逻辑隔离；首版可同一 Python 环境启动 API/scheduler/CPU worker，但生命周期和模块边界仍分离。
4. 任务采用 DB 持久队列、lease、heartbeat、transactional outbox；GPU 重任务默认由机器配置显式声明为独占并发 1，H3 正式任务一任务一 Worker 后回收。
5. 默认监听 127.0.0.1；首版远程 transport 不实例化、不暴露 API key、费用或云调用。

## 后果

- SQLite 写事务必须短小，媒体 probe/hash/FFmpeg 必须在事务外完成。
- 每个 migration 需要升级预检、备份要求、验证 SQL 和恢复说明。
- 未来可替换存储或拆分 Worker，但当前不引入 Redis/Celery/PostgreSQL/Electron。

## 回滚

架构变更通过新 ADR；应用升级使用新目录/环境。不可逆 migration 通过迁移前数据库备份回滚，不伪造 SQLite downgrade。

