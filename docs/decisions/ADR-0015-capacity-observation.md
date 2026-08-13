# ADR-0015：本机队列产能只读观测

状态：Accepted（2026-08-14）

G9-09 先实现真实 SQLite 队列观测接口 `GET /api/v1/capacity/snapshot`。它统计已持久化 Job/Attempt 的状态、排队年龄、Worker、GPU_H3 并发和近 24 小时完成数，明确标记 `OBSERVED_NOT_BENCHMARKED`。接口不创建任务、不 claim lease、不启动 Worker、不触碰 ComfyUI、不连接 webhook；loopback automation/webhook 仍未实现，不能以观测数据冒充性能基准或远程通知能力。

证据：`test_capacity_snapshot.py`、任务页只读面板、`G9_progress_report.md`。
