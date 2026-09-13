# Phase 1 / K06 候选数量与预算一致性验收（2026-09-12）

## 结论

K06 已完成。Profile 的候选请求在预检前一次解析为 1—4 的产品有效值；请求值、生效值、
最大值和调整原因一并进入预检指纹与冻结 workflow。Worker 不再静默把 6/8/16 改成 4，
收到未冻结的越界值会返回 `VIDEO_TARGET_TAKE_COUNT_INVALID`。

## 实施范围

- `episode_production_runs.py`：统一解析三种模式的请求/生效候选数，产品上限保持 4；磁盘
  估算、预检指纹和 workflow 快照使用同一生效值。
- `episode_worker_actions.py`：严格校验 1—4；分别报告总目标、当前有效、活动候选和技术
  重试数；已有有效和在途候选只填缺口。
- 本集概览 API 暴露同一 `available_mode_policies` 只读结果。
- 本集生成设置显示每镜生效数；Profile 超限时显示请求值与“按产品上限 4 个执行”，并
  明确候选数不是 GPU 并发数。
- OpenAPI 与 TypeScript 客户端由 `scripts/generate_client.py` 重新生成。

## 验收证据

`python -m pytest -q apps/api/tests/test_episode_production_modes.py apps/api/tests/test_episode_worker_actions.py`

结果：40 passed。覆盖 Profile 候选 1/2/4/6/8/16、三种模式冻结、Worker 越界拒绝、目标
1/2/4、失败技术重试、已有 3 + 在途 1 时不补拍，以及依赖 Job 计数。

`npm test -- --run src/features/episode-production-v2/EpisodeProductionWorkspace.test.tsx`

结果：17 passed。覆盖默认模式启动、请求 8/16 生效 4 的可见提示和模式切换。

Ruff 与 `git diff --check` 通过；OpenAPI/客户端生成成功。`npm run build` 的 K06 类型路径
已越过候选策略字段检查，随后被既存的 `LocalLLMConfigurationPanel.tsx:181` 可选 preset
model 类型错误阻塞；该错误不在 K06 改动文件或生成 diff 中，保留给对应配置工作包处理。

## 未扩大范围

没有提高默认成本、没有把候选数当并发数、没有新建预算中心，也没有改写运行中的旧快照。
