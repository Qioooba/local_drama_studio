# ADR-0011：Legacy 项目迁移延期

- 状态：Accepted for this task; implementation deferred to G11
- 需求：FR-PRJ-005、OUT-001、OUT-006

《老屋灯火》及其他旧目录迁移不属于 G0—G10。核心只实现 v2 标准项目包协议，不猜测旧目录、CUT/SHOT ID 或历史审批映射。G10 通过后才能启动独立 importer plugin，并必须提供 dry-run、映射、回滚和不修改核心领域模型的证据。

