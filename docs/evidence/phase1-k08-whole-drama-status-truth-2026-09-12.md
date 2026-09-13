# Phase 1 / K08 整剧状态与调度真值验收（2026-09-12）

## 结论

K08 已完成。整剧状态不再把失败、暂停、取消、未开始统一显示为 READY；主状态与原始
分项计数同时返回。整剧启动区分 0/N、部分 N 和 N/N，且 UI 明确“已调度不等于已生成”。

## 实施范围

- `whole_drama_orchestrator.py`：新增纯状态真值归约；保留每种 run 状态计数。
- 分集与 run 的关联优先读取持久 run task 的完整 episode ID，并可从任意 workflow node
  的 metadata 精确回退；移除 `nodes[0]` 假设和短 ID `LIKE`。
- 整剧 run 响应增加 `dispatch_status`、`dispatch_reason`、`blocked_count`。
- 一键制作界面分别显示未启动、部分启动、全部调度，并展示分项状态计数。
- API schema、OpenAPI 和生成客户端同步更新。

## 验收证据

`python -m pytest -q apps/api/tests/test_whole_drama_orchestrator.py apps/api/tests/test_automation_whole_drama.py`

结果：27 passed。覆盖无集、未开始、全成功、全失败、全暂停、全取消、运行+失败、成功+
未开始，0/3、1/3、3/3 调度，以及 workflow 节点重排后的精确分集关联。

`npm test -- --run src/features/pipeline/OneClickPipelineWorkbench.test.tsx src/features/episode-production-v2/EpisodeProductionWorkspace.test.tsx`

结果：22 passed。覆盖 0/N 不显示成功、分项计数、K06 候选提示及原本集交互回归。

Ruff、客户端生成与 `git diff --check` 通过。

## 未扩大范围

没有增加数据库展示枚举、没有改写历史 run 状态、没有新建任务中心，也没有在此处另写
媒体资格 SQL；当前产物资格仍留给 K18 收口。
