# ADR-0003：标准项目目录与 legacy 边界

- 状态：Accepted for G0 implementation baseline
- 需求：FR-PRJ-001、FR-PRJ-002、FR-PRJ-005、NFR-SEC-002

## 决策

采用 03 文档定义的 v2 项目目录和 `project.json`，动态业务状态只在 SQLite。目录名不表达审批状态，不创建 `pending/approved/rejected` 权威目录。标准 v2 package import/export 与 legacy importer 分离；旧 CUT/SHOT 映射只允许在 G11 另行立项，本次 OpenAPI 不暴露 legacy scanner/mapping。

## 后果

既有 `F:\AI_Projects\h3\projects` 只作为只读背景和测试素材来源，不被核心模型扫描或迁移。任何导入都先 staging、schema/hash/disk 预检、commit，再注册媒体。

