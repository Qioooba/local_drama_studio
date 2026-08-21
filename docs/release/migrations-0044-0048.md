# 0044–0048 升级、备份、恢复与包兼容 Runbook

机器可读版本权威是同目录 `migration-contract.json`。当前源码的 Alembic 唯一预期 head 为 `0048_asset_proposals`；正式库是否已到达该版本必须现场只读查询，不能依据本文假定。

## 升级前

1. 停止 LocalDramaStudio API/Worker 新写入；记录 RUNNING jobs 的可恢复状态，不操作用户自行启动的 ComfyUI。
2. 使用 SQLite online backup 或迁移 preflight 创建 `pre_migration_*.sqlite3`，记录源 revision、SHA-256、字节数和 `PRAGMA integrity_check=ok`。不要在 WAL 正写入时裸复制单个主文件。
3. 复制备份到隔离目录，先运行 `scripts/release_rehearsal.py --source <backup> --root <new-empty-dir> --output <evidence.json>`。脚本只升级副本并另建精确恢复副本。
4. 对正式发布 commit 运行 migration、package、invariant 和 safe API tests；归档 commit、锁文件 hash、实际 Alembic heads。

## 0044 Shot Scene / Groups

- 确认 `shots.scene_id` 可空，旧镜头保持可读；group/member 索引存在。
- SQLite 必须使用 batch-safe additive migration，不得临时对正式库执行不支持的 `ALTER TABLE ADD FOREIGN KEY`。
- smoke：旧 episode 无 group 正常打开；assignment/reorder 后镜头 identity 不变。

## 0045 QC Policy

- 确认 project/episode/shot policy set/version 可解析，旧项目使用受控默认。
- machine QC 不创建 human approval，失败候选和 reroll lineage 不删除。
- bounded auto-reroll 使用解析后的版本化上限与类别。

## 0046 Director Recipes

- recipe/version/binding 必须同项目，version hash 可复验。
- 旧项目无 binding 时保持显式未选择/缺省，不凭空绑定 recipe。

## 0047 Shot Editing

- smoke reorder/split：server 裁决 order，expected revision/plan hash 冲突可见。
- split parent 只归档不删除；source range、资产绑定、DirectorIntent revision 和旧 variants 可追溯。

## 0048 Asset Proposals

- 旧 breakdown 无 proposal 时默认空列表。
- decision 保留 evidence、revision 和 audit；MERGE/CREATE/REJECT 不做 destructive identity merge。
- `breakdown_draft_id` 可空用于 package copy，新 breakdown apply 仍记录真实 draft。

## Project Package 兼容

当前 manifest/state schema 是 `localdrama.project-package.v2` / `localdrama.project-state.v2`。导出包含 0042–0048 的项目范围事实：asset states/references/bindings、preferences、scene/groups、QC policies、recipes/binding、asset proposals；镜头编辑历史通过既有 shot/revision/variant 事实保持。

验收：新项目 export → import copy → re-export；旧 v2 缺 optional 集合时使用空集合/safe default；project/episode/scene/shot/asset/media ID 重写后引用闭合；media hash/lineage 不变；未知 schema 返回 `PROJECT_PACKAGE_SCHEMA_UNSUPPORTED`，不得 silent drop。

## 回滚/恢复

默认回滚是停止写入后恢复已验证的 pre-migration backup，并检出匹配代码。恢复后期望 revision 是“备份记录的 revision”，不是硬编码 0039、0041 或当前 0048。不要对含重要新数据的正式库直接 downgrade；若升级后已创作，先冻结写入并为新增事实编写 export/repair 计划。

恢复必须验证 integrity、SHA-256、项目树/media hash sample、旧项目、旧 variants/frame anchors/timeline 和 package import/export。只有随后重新升级的隔离副本才应达到 `migration-contract.json.expected_heads`。
