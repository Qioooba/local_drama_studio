# G9 进度报告（自动化基础通过，正式退出待补证）

状态：`IN_PROGRESS / AUTOMATED_BASELINE_PASS`。本文件不是 G9 exit report。

已完成：

- React Flow 业务画布、MiniMap、缩放/平移和状态节点。
- 画布本地搜索和上游/下游聚焦：对业务 DAG 做传递闭包筛选，仅改变可视节点与 edges，不持久化、不改变业务依赖。
- G9-09 只读产能快照：`GET /api/v1/capacity/snapshot` 基于真实 SQLite Job/Attempt 观测排队、Worker、GPU_H3 并发与近 24h 完成数，标记 `OBSERVED_NOT_BENCHMARKED`；不创建任务、不 claim lease、不触碰 runtime/network。
- 任务页 1024×768 真实 UAT：快照显示 `queued=1`、`active_attempt=0`、`GPU=0/1`、近 24h 完成 `12`；零 console/page error、零失败响应、零水平溢出；视觉证据仅为 720px WebP。
- G9-07 键盘替代操作：画布提供独立键盘节点按钮列表；Enter/Space 可选中节点并驱动聚焦/预检，不编辑业务边或布局；1024×768 验收 10 个键盘按钮、零错误、零溢出。
- G9-06 route/selection 同步：画布节点和键盘按钮选择会写入现有 `shot` URL 参数，刷新后由同一 read model 恢复选中 shot；1024×768 真实验收无错误/失败响应；证据 `docs/evidence/g9/canvas-route-sync-visual-review-2026-08-14.json`。
- G9 accessibility baseline：1024×768 读取 `业务画布`、`键盘节点列表`、搜索输入的可访问名称；可见 focusable 控件 28 个，节点按钮与选中状态可见，零 console/page error、零失败响应、零水平溢出；证据 `docs/evidence/g9/canvas-accessibility-visual-review-2026-08-14.json`。这不是完整可访问性清单或性能退出。
- episode/shot lazy graph read model；节点汇总 take、variant、blocker、active/failed job 和连续性约束。
- G9-08 只读节点细节：画布节点从 SQLite 汇总 variant lineage（含 stale/branch reason）、experiment cell 成功/失败进度及相邻 transition constraint；选中节点展示真实摘要，空数据明确显示暂无，不创建 variant/job、不连接 runtime。
- 视觉布局独立持久化，乐观并发；布局提交不能增加、删除或改变业务 edges。
- NODE/FROM/TO/RANGE 执行 preflight，只生成计划，不直接绕过人工门禁提交任务。
- 300 可见节点硬上限和 GPU 重任务并发 1 估算。
- migration/OpenAPI/generated client、112 API tests（4 Comfy live deselected）、Ruff/mypy、web build/Vitest 全绿。

未完成：

- 正式 100—300 可见节点浏览器性能证据、键盘/可访问性完整清单和阶段截图。
- G9-09 的 loopback automation/webhook 仍未实现；产能观测已完成但不等价于 benchmark 或 webhook 能力。
- G6 H3/本地 LLM 门禁仍未全部通过，不能宣称顺序门禁整体完成。
