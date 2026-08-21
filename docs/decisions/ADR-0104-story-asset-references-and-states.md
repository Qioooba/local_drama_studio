# ADR-0104：Story Asset 以引用和状态扩展同一身份

- 状态：Accepted
- 日期：2026-08-20
- 对应：xinjihua 02 §80 ADR-004、§81；03 P2～P3

## Context

角色、场景、道具和服装已有 `story_assets` 身份及 canonical media。生产还需要 HERO、多视图、光照/表情参考，以及换装、受伤、DAY/NIGHT 等剧情状态，同时必须兼容旧 canonical 数据。

## Decision

保持 `story_assets` 为唯一身份；以 additive 0042 增加 `story_asset_references`、`story_asset_states`、episode/shot state binding，并让 reference 指向不可变 media version。canonical HERO backfill 和 projection 保持旧代码可读。archive reference 只改变可用性，不删除媒体历史。

## Rejected alternatives

- 为每种状态建立新角色/场景：拒绝，因为会破坏身份与使用统计。
- 把多参考全部塞进 JSON：拒绝，因为无法约束 project、state、role 和历史。
- 删除 canonical 字段直接切新表：拒绝，因为旧项目与 package 会失效。

## Consequences

UI 可围绕同一身份展示多参考和状态继承；命令必须验证 media/asset/state/shot 同项目。package export/import 必须版本化包含新事实并为旧包提供 safe defaults。

## 事实来源

`0042_asset_bible_states_references.py`、`application/commands/asset_bible.py`、`asset_bible_repository.py`、`application/project_packages.py`、`test_asset_bible.py`、`test_migration_0042.py`。
