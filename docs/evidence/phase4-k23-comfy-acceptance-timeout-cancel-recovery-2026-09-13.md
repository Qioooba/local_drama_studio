# Phase 4 / K23：Comfy 受理、超时、取消与恢复

日期：2026-09-13

## 路线盘点结论

- 整集/整剧与 Director 的正式生产仍走 `GPU_H3` + `ComfyGenerationService`，使用持久 Job、Attempt、provider prompt ID、租约和 artifact 生命周期。
- Model Platform V2 的 Comfy handler 当前只由 Quick Create 页面上的显式“V2 试运行”入口使用；它没有替换整集/整剧生产。其 5—300 秒短预算继续作为试运行合同，不被误当成 H3 正式视频生产预算。
- 正式 `GPU_H3` 生产预算默认 3600 秒；测试确认 301 秒时仍可继续并成功，不沿用短 smoke 的 300 秒上限。

## 修复与核验

- 在调用 `/prompt` 前持久 heartbeat 阶段 `SUBMITTING_TO_PROVIDER`。Worker/会话租约在这一窗口失效时，任务进入 `NEEDS_ATTENTION`，不会自动重投。
- `/prompt` 的超时或不确定网络错误会调用 `mark_provider_acceptance_unknown`，释放本地租约并明确隔离；直接 retry 返回 `PROVIDER_ACCEPTANCE_RECONCILIATION_REQUIRED`。
- 已获得 prompt ID 后，Attempt provider 字段与 Job 的编译图/运行覆盖/参数影响/prompt 证据在一个数据库事务中提交。
- 已知 prompt ID 的 history、下载和 artifact 注册可重复执行；下载失败保持原 Attempt 与 prompt，不重新采样。
- Job 已取消时，外部迟到成功可以登记隔离 artifact 供审计，但返回 `CANCELLED`，不执行资产/关键帧完成器，也不晋升 GenerationVariant 媒体。
- 只有目标 prompt 是 Comfy 唯一 running prompt 时才允许全局 interrupt；共享运行时存在其他 running prompt 时返回 `COMFY_INTERRUPT_OWNERSHIP_UNSAFE`。
- provider busy 使用独立、持久的 1800 秒连续无响应宽限；正常 provider 事件会重置宽限窗口。总执行预算仍有界。

## 故障断言

`apps/api/tests/test_comfy_jobs.py` 新增并通过：

- 回包丢失：submit 次数为 1，任务隔离为 `NEEDS_ATTENTION`，不能盲 retry。
- 回执前崩溃：`SUBMITTING_TO_PROVIDER` 的租约恢复被判定为 uncertain side effect。
- 301 秒完成：600 秒生产预算下成功，不被短 smoke 上限截断。
- 共享 runtime 取消：不调用全局 interrupt；独占时才中断目标。
- 取消后迟到成功：Job 保持 `CANCELLED`，无媒体晋升。
- 下载失败：第二次查询相同 prompt 并成功登记，不触发新提交。
- Worker 重启/已知 provider success：既有后台恢复扫描从原 prompt history 恢复 artifact。

## 回归结果

- `ruff check`：K23 修改文件全部通过。
- `test_comfy_jobs.py`：18 项通过。
- Comfy、Job、Worker session、V2 profile/binding、GPU runtime orchestration 组合回归均通过；组合命令末尾仅 `test_gpu_lifecycle_adapters.py::test_default_registry_keeps_ollama_lifecycle_endpoint_independent_from_inference_provider` 失败。该断言检查 Ollama adapter 的环境装配，K23 未修改对应文件或配置，且失败与 Comfy 接受/恢复链无关。

## 安全边界

全部为临时数据库、文件夹和 mock provider 故障注入；未连接真实 Comfy、未启动 GPU、未下载模型、未触碰生产数据库。V2 仍维持显式试运行边界，正式切换整集生产前必须另行提供长任务预算与同等级 provider receipt/reconcile 合同。
