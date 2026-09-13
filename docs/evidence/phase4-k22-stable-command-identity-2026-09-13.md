# Phase 4 / K22：整集与整剧稳定命令身份

日期：2026-09-13

## 结论

K22 已完成本地实现与回归验证。整剧启动现在要求调用方提供稳定的 `Idempotency-Key`；一次用户启动产生一个父命令键，网络失败重试继续使用该键，各分集命令键由父键和完整 episode ID 确定性派生。整集启动也在重新执行磁盘/状态预检之前查询已受理结果。

## 关键事实

- `WholeDramaOrchestratorService.run` 持久化父命令的请求摘要和完整受理响应；相同键、相同 payload 返回原有子运行关系并标记 `idempotent_replay=true`。
- 相同键配不同 payload 返回 `IDEMPOTENCY_PAYLOAD_MISMATCH`，不会启动新任务。
- 父命令的每集子键为 `<parent>:episode:<episode-id>`，不依赖节点顺序、短码或临时随机数。
- `EpisodeProductionRunService.start` 先查询 `command_idempotencies`，命中后直接恢复原 run；磁盘余量或运行状态随后变化不会阻止原意图重放。
- 前端在一次启动失败后保留命令键，成功后清除；下一次明确启动会创建新键，因此不会把参数相同的未来新拍永久折叠。
- API 的整剧启动路由缺少 `Idempotency-Key` 时返回 422。

## 验证

- `ruff check`：K22 相关 Python 文件全部通过。
- `pytest apps/api/tests/test_episode_production_runs.py apps/api/tests/test_whole_drama_orchestrator.py apps/api/tests/test_episode_one_click_pipeline.py apps/api/tests/test_automation_workflows.py -q`：全部通过。
- `pytest apps/api/tests/test_whole_drama_orchestrator.py -q`：15 项通过，包含并发父命令重放、payload 冲突和必填请求头。
- `vitest run src/features/pipeline/OneClickPipelineWorkbench.test.tsx`：6 项通过。
- `tsc --noEmit`：K22 新增签名错误已清除；仍仅有既知、无关的 `LocalLLMConfigurationPanel.tsx:181` 类型错误。

## 安全边界

验证使用临时测试数据库和 mock 调度，不调用真实模型、不启动 GPU 工作流、不修改生产数据库。现有 `command_idempotencies` 表与既有子运行记录继续兼容。
