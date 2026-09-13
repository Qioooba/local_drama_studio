# Phase 2 / K10：显式原稿覆盖与超预算止损证据

- 日期：2026-09-12（UTC+08:00）
- 基线提交：`e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3`
- 隔离：临时 SQLite、临时项目目录、测试 LLM；未调用真实模型、GPU、网络或生产服务

## 复现结论

原实现用 `chapters[:60]` / `fallback_groups[:60]` 静默截断分析单元，同时 `_episode_prompt` 只发送单元前 24,000 个 Unicode 字符；完成态仍显示“全剧规划完成”。因此第 61 章后的尾部和超长单元尾部没有证据，也没有续接位置。

## 最小改造

1. 启动快照冻结用户已提交的正文授权范围与源 SHA；已有导入只使用 `SOURCE_BODY_RANGE`，不自动扩大为全文。
2. 60 保留为单批安全上限，但不再丢弃总单元目录；完成覆盖、未处理覆盖、排除原因和唯一续接位置写入 `pipeline.source-coverage.v1`。
3. 单元超过 24,000 字符时记录实际提交字符数、未处理字符数和单元内续接偏移；不把字符数描述成 token。
4. 覆盖按成功单元的段落区间并集合并，重叠不重复累计；失败/未完成窗口不计入覆盖，并在失败任务草案中保存当前覆盖证据。
5. 全覆盖显示“授权原稿范围规划完成”；部分覆盖保持技术执行成功但质量为 `REVIEW_REQUIRED`，禁止自动应用，界面显示“原稿部分完成”和准确续接位置。
6. 旧任务缺少授权范围时停止并要求重新发起；源 SHA 与启动快照不一致时以 `PIPELINE_SOURCE_CHANGED` 拒绝旧游标。

## 验收覆盖

- 61 / 120 / 121 章：第 61 单元起进入 `BATCH_EPISODE_LIMIT` 未处理区间。
- 超 24,000 字符章节、超长无章节单段、emoji：实际提交恰为 24,000 个 Unicode 字符，唯一尾部事件不被误称已处理。
- 重叠窗口：覆盖并集为 1–6，共 6 段，不重复累计。
- 只授权前 10 章：授权范围内 10 个单元可得到 `FULL`，授权外正文不冒充本次待处理范围。
- 失败窗口：只累计此前成功窗口，失败窗口标记 `WINDOW_NOT_COMPLETED`。
- 源 SHA 变化：旧任务失败且不复用游标。

## 分层回归

```powershell
.\.venv\Scripts\python.exe -m ruff check apps/api/local_drama/application/pipeline_orchestrator.py apps/api/local_drama/application/story_pipeline_ai.py apps/api/tests/test_pipeline_orchestrator.py
.\.venv\Scripts\python.exe -m pytest -q apps/api/tests/test_pipeline_orchestrator.py apps/api/tests/test_story_pipeline_ai.py apps/api/tests/test_story_pipeline_composition.py
pnpm --filter local-drama-studio-web test -- src/features/pipeline/OneClickPipelineWorkbench.test.tsx
git diff --check
```

结果：Ruff 通过；45 项 API pytest 通过；6 项界面 Vitest 通过；`git diff --check` 通过。

补充类型检查 `pnpm --filter local-drama-studio-web exec tsc --noEmit` 仍只报告本轮之前已记录的 `LocalLLMConfigurationPanel.tsx:181` 可选 `preset.model` 问题；K10 修改文件未产生新的 TypeScript 错误。
