# G9 进度报告（自动化基础通过，正式退出待补证）

状态：`IN_PROGRESS / AUTOMATED_BASELINE_PASS`。本文件不是 G9 exit report。

已完成：

- React Flow 业务画布、MiniMap、缩放/平移和状态节点。
- episode/shot lazy graph read model；节点汇总 take、variant、blocker、active/failed job 和连续性约束。
- 视觉布局独立持久化，乐观并发；布局提交不能增加、删除或改变业务 edges。
- NODE/FROM/TO/RANGE 执行 preflight，只生成计划，不直接绕过人工门禁提交任务。
- 300 可见节点硬上限和 GPU 重任务并发 1 估算。
- migration/OpenAPI/generated client、34 API tests、Ruff/mypy、web build/Vitest 全绿。

未完成：

- 正式三视图上下文同步 UAT、100—300 可见节点浏览器性能证据、键盘/可访问性完整清单和阶段截图。
- G9-09 的 loopback automation/webhook 与产能看板属于 P1，后续继续实现。
- G6 H3/本地 LLM 门禁仍未全部通过，不能宣称顺序门禁整体完成。
