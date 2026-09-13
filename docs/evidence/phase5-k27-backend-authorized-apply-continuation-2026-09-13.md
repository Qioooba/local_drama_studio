# Phase 5 / K27：后端持久化的规划应用续接

日期：2026-09-13

## 结论

规划草案的应用续接已从浏览器挂载副作用迁移到后端持久 Job。一次启动只授权明确的文本 sections；媒体生成、审核和发布不在授权范围内。旧客户端和未授权历史运行默认停在草案。

## 改造事实

- `StartPipelineRequest.application_authorization` 明确区分 `DRAFT_ONLY` 与 `APPLY_SELECTED_SECTIONS`，后者必须列出合法 sections。
- 启动事务同时创建草案 Job 和依赖它的 `STORY_PIPELINE_APPLY` Job；后者只有在草案 Job 成功后才可领取，进程重启和关页不丢失。
- 续接 Job 调用原 `preview_pipeline_apply` / `apply_pipeline` 命令，不直接写 episodes、资产或创作记忆表。
- 生成完成时冻结授权草案 SHA；草案后改、来源变化、质量门禁或 revision 冲突都会暂停应用并留下 Job 错误诊断。
- 已应用后重放返回 idempotent replay，不新增版本或资产。
- 草案成功且应用尚未领取时，可通过原取消入口撤销续接授权并取消 Job，保留已生成草案。
- 前端删除了 READY 历史记录挂载时自动 apply 的 `useEffect`；页面只显示状态或发送显式用户命令。

## 验证

- `pytest apps/api/tests/test_pipeline_orchestrator.py -q`：13 项通过。
  - 两个不同 Worker 实例分别完成草案与应用，模拟关页/进程重启。
  - 草案后改被 `PIPELINE_AUTHORIZED_DRAFT_CHANGED` 阻断。
  - 已应用命令幂等回放不增加 revision。
  - 应用前撤销后，续接 Job 为 `CANCELLED` 且正式数据未应用。
- `vitest run ...OneClickPipelineWorkbench.test.tsx ...StoryboardBatchWorkbench.test.tsx`：9 项通过；READY 历史记录挂载不会触发 preview/apply。
- `ruff check`：相关后端文件通过。
- OpenAPI 与生成客户端已更新。
- `tsc --noEmit`：本包新增类型错误为 0；仍仅有既存无关错误 `LocalLLMConfigurationPanel.tsx:181`。

## 兼容与回滚

不携带新授权字段的调用等同 `DRAFT_ONLY`。关闭自动续接仍保留草案、手动影响预览和手动应用入口；撤销只取消未完成续接，不删除历史草案或已生成实体。
