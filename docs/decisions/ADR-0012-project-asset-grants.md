# ADR-0012：跨项目工作区资产授权

- 需求：FR-AST-001
- 状态：Accepted

## 决策

公共工作区资产不复制到目标项目，也不改变源 `MediaVersion`。目标项目只能通过 `ProjectAssetGrant` 引用来源项目已经显式授权的 VERIFIED 媒体；Grant 冻结来源授权 revision、SHA-256、大小、访问模式（`READ_ONLY` 或 `DERIVED`）和审计事件。

Grant 查询持续比较来源授权状态、revision、媒体 hash/size 与冻结快照。来源撤回或内容变化只产生明确影响状态并阻止使用；撤回 Grant 也保留原记录和原因。

## 取舍

首版不做跨项目文件复制、在线资产库或远程 URL。这样保持 LOCAL_ONLY 与用户素材边界，目标项目可以复用已授权资产，同时不会把平台变成隐式素材分发器。
