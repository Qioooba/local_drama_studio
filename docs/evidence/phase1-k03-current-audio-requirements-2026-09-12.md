# Phase 1 / K03：当前音轨需求实施记录

- 日期：2026-09-12
- 状态：`FIX_MINIMAL`
- 范围：仅 `_field_cue_counts` 当前事实读取与损坏关系诊断。
- 安全边界：只读业务逻辑和隔离 SQLite 测试；未删除历史 revision，未修改生产数据库。

## 红灯证据

隔离数据库构造：旧 revision 有 BGM、当前 revision 删除 cue、归档镜头有 SFX、另一当前有效镜头有 BGM。修复前错误返回 `BGM + SFX`；当前 revision 丢失时也静默当作无 cue。

## 最小修复

- 从未归档 Shot 出发，只通过 `shots.current_revision_id` 读取属于该 Shot 的当前 revision。
- `null`、空字符串、空列表、空对象继续表示没有 cue。
- 当前指针为空或指向不存在/不属于该 Shot 的 revision 时，返回 `SHOT_CURRENT_REVISION_MISSING`，不回退扫描历史。
- 不删除或改写任何旧 revision，不改变可选 cue 表兼容逻辑。

## 验收

- 新反例与既有 G8 状态测试：9 passed。
- 修复后只要求 `BGM`，`music_cues=1`、`sfx_cues=0`、`caption_cues=0`。
- 测试前后 `shot_revisions` 数量一致。
- Ruff 与 `git diff --check` 通过；仅既有依赖弃用警告。
