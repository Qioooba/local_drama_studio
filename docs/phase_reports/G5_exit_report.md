# G5 阶段退出报告

- 基线：G4 `0003_g4_review_selection`
- migration：`0004_g5_job_queue`
- 阶段结论：PASS
- 证据：`docs/evidence/g5/g5_validation.txt`
- 下一阶段：G6 ComfyUI 与本地生成闭环

## 已交付

- 持久 Jobs/JobAttempts/Dependencies：事务入队、优先级、最大尝试数、channel、input snapshot 与 immutable command 语义。
- 强制 Idempotency-Key 与 payload hash；双击同一命令只产生一 Job，重试和 clone 明确分离。
- Worker claim/lease/heartbeat：不可猜 token、worker 单活动 Attempt、默认 60 秒 lease、过期写入拒绝。
- reconcile/orphan：进程强杀或 lease 过期后将 Attempt 标记 ORPHANED；无已知 provider side effect 时重新入队，否则 NEEDS_ATTENTION。
- cancel/retry/backoff/clone 与稳定错误分类；artifacts 完成文件 hash、路径沙箱、partial/symlink/越界拒绝和幂等注册。
- Outbox + cursor SSE，任务台实时读取真实 Job 状态；浏览器证据包含 QUEUED、SUCCEEDED 与 SSE-follow 刷新。
- Generation matrix plan/estimate/confirm/expand：轴组合、资源估算、懒展开、逐 cell Job 与逐 cell cancel。
- 独立 LOCAL_ONLY worker：真实 FFmpeg/FFprobe 生成 proxy/thumbnail，`scripts/run_worker.py` 可执行 CPU/媒体任务。
- OpenAPI snapshot 与 TypeScript client 已按 G5 routes 重新生成。

## 退出演示与门禁

- 20 个 CPU 测试任务真实入队、依序 claim/complete，公平性/优先级/单 worker 约束通过。
- 页面关闭不影响 SQLite 队列；API 重启后的持久状态和 SSE-follow 任务台通过真实浏览器验证。
- 独立 worker 进程被 kill 后，reconcile 实测 ORPHANED → QUEUED，无重复 Attempt。
- FFmpeg/FFprobe proxy/thumbnail 与 artifact verify 通过；损坏/partial/越界文件不会注册。
- 全量 API/domain/migration/media/review/queue 测试：27 passed，0 failed；Ruff、mypy 50 个 Python 源文件、Web build、Vitest 全部通过。
- G0 基线复核仍为 86 FR、14 NFR、85 TC；本阶段只推进 G5 映射，不声称全量发布完成。

## 明确未完成

- G6/G7：真实本地 LLM 拆镜、ComfyUI semantic generation、Profile publish/capability truthfulness、变体/实验/首尾帧生成闭环。
- G8：连续性增强、视频审核、音频字幕、增强链、时间线、交付与 verify。
- G9/G10：ComfyUI Lab/业务画布、诊断扩展、规模/性能/安全完整矩阵、全链 UAT、安装升级回滚、SBOM 和 go/no-go。
- 云 Provider、API Key、计费、多租户和 G11 legacy migration 仍未实现。

## 回滚与恢复

G5 migration 由 `scripts/migrate.py` 在 SQLite WAL 上执行 online preflight backup，备份以 `PRAGMA integrity_check` 验证。队列状态、Attempt、Outbox 和 artifact registration 均以 SQLite 为权威；worker sandbox 输出不可直接成为媒体版本，必须先 hash/probe/register。恢复后重新运行 migration head、integrity、outbox/read model、worker recovery 和 G5 测试。

## 门禁结论

`PASS — G5 persistent queue, local worker, SSE and kill/recovery evidence are green; G6 may start.`
