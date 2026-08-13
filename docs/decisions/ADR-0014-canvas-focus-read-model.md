# ADR-0014：画布搜索与上下游聚焦只作用于 read model

状态：Accepted（2026-08-14）

G9-05/G9-07/G9-06 的搜索、上游和下游聚焦在前端对已读取的业务 DAG 做传递闭包筛选，并提供独立键盘节点按钮列表。筛选与键盘选择不写数据库、不改变 `edges`、不提交运行计划，也不改变 URL 中的项目/集/镜头上下文；清除筛选即可恢复完整 read model。节点预检仍由后端执行，只有显式选择节点后才可运行。

证据：`focusCanvasNodeIds` 单元测试、G9 三档 Playwright 验收和 `g9_validation.txt`。
