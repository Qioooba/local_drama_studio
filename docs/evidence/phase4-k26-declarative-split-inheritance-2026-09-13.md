# Phase 4 / K26：拆镜的声明性继承与结果隔离

日期：2026-09-13

## 结论

拆镜现在会完整复制合法的声明性绑定（asset、role、显式 asset state、identity pack version），但不会复制 GenerationIntent/Variant、工作媒体槽、审批选择、QC 或任何已生成结果。台词和动作归属必须在拆分命令中明确声明。

## 改造事实

- `_copy_asset_bindings` 复制 `asset_state_id` 与 `identity_pack_version_id`，不再只复制 asset ID/role。
- plan 在写入前验证：资产必须 ACTIVE；显式状态必须 ACTIVE 且属于对应资产；身份包版本必须属于对应角色且为 APPROVED/SUPERSEDED。撤销、错绑或 DRAFT 引用使 plan 无效。
- `ShotSplitCommand` 新增：
  - `dialogue_destination`: `FIRST | SECOND | SOURCE_ONLY`
  - `action_destination`: `FIRST | SECOND | BOTH | SOURCE_ONLY`
- canonical `dialogue_lines` 只会移动到一个指定子镜头，或留在已归档来源镜头作为历史；不会复制成两份。
- 两个子 revision 的自由文本 `dialogue`/`action` 按同一显式策略清空或保留，并在 `split_lineage` 中记录分配策略、source shot ID 和 source revision ID。
- 来源镜头继续非破坏归档，子镜头保留 `source_shot_id`；旧 timeline 继续标记 STALE。
- 编辑界面增加台词/动作归属选择，并在本地草稿中的 split command 固化该选择。

## 验证

- `pytest apps/api/tests/test_shot_editing.py -q`：3 项通过。
  - A/B 子镜头保留相同 identity pack version 与 asset state。
  - 台词只归属 A，B 的 dialogue 清空；动作按显式 BOTH 保留。
  - 子镜头无 generation intent；来源 revision 和谱系可回查；timeline 失效。
  - RETIRED 状态与 DRAFT 身份包不能经拆镜复制绕过验证。
- `vitest run src/features/projects/StoryboardBatchWorkbench.test.tsx`：3 项通过，确认 UI 生成显式分配字段并保留既有自动编号/撤销流程。
- OpenAPI 与生成客户端已重新生成。
- `ruff check`：修改文件通过。

## 兼容边界

旧客户端缺少两个必填分配字段会收到 422，必须先刷新前端并重新预览；这是为了避免以隐式默认值重复台词或动作。已存在的历史拆镜记录不修改、不删除。
