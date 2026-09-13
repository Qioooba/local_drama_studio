# Phase 4 / K25：Beat Replan 稳定镜头匹配

日期：2026-09-13

## 结论

Beat Replan 已移除按数组下标配对的实现。建议现在优先使用当前 Beat 内合法的 `shot_id`，其次使用唯一的声明性 `source_ref` / `source_shot_ref` / `beat_ref`；只有显式 `operation=ADD` 或 `is_new=true` 的项才视为新镜头。

## 安全规则

- 建议中的 `shot_id` 必须属于当前 episode 的当前 selected Beat；跨集、组外或伪造 ID 产生 `SHOT_ID_OUT_OF_SCOPE`，plan 不可应用。
- 同一 ID 重复引用产生 `SHOT_ID_DUPLICATED`。
- 稳定来源引用只能唯一匹配；零个或多个候选产生 `SHOT_SOURCE_REF_AMBIGUOUS`。
- 没有 ID/来源引用且未显式声明新增的项产生 `SHOT_MATCH_ID_REQUIRED`。系统仍展示阻塞 diff，绝不按相邻位置静默套到旧镜头。
- diff 顺序由提案声明顺序决定，因此 A/X/B/C、重排和末尾新增都不会把 X 之后的 B/C 错配。
- KEEP 镜头继续保留原 shot ID 与 current revision；MODIFY 才创建新 revision；既有 plan hash、group revision CAS、冻结保护和命令幂等保持不变。

## 验证

`pytest apps/api/tests/test_beat_replan.py -q`：4 项通过，覆盖：

- 原有 KEEP/MODIFY/PROTECTED/ADD、安全应用、审计与幂等。
- 过期 plan hash 拒绝应用。
- 中部插镜并重排 A/X/C/B：A/B/C 的 ID 和 current revision 均保持，只有 X 新建。
- 伪造 ID 与模型漏 ID：plan `valid=false`，不能自动应用。

`ruff check`：服务与测试文件通过。

## 兼容边界

旧的、完全不含稳定身份或显式新增标记的 AI 草稿仍可只读预览，但必须重新分析或人工补齐身份后才能应用；不会用旧的下标算法猜测配对。
