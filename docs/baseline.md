# G0 规格基线记录

## 适用范围

本基线锁定 `F:\AI_Projects\h3\LocalDramaStudio_Blueprint_v2` 中 00—10、12—17 的实现语义。11 号文档是延期的旧项目迁移专项，本次明确排除，不读取、不实现、不作为通用核心验收条件。

权威解释顺序：00 → 01 → 02 → 03 → 04 → 05 → 06 → 07 → 08 → 09 → 10 → 12 → 13 → 14 → 15 → 16 → 17。

## 已确认的产品边界

- 通用短剧生产系统，完整支持 60 集；不得围绕任何既有家庭剧目录建模。
- 三视图：生产视图、业务画布、ComfyUI Lab；Designer 与 Production Comfy 隔离。
- SQLite/WAL 记录实体、状态、选择、审核、任务和审计；文件系统保存媒体；cache 可重建。
- MediaVersion、ReviewDecision、WorkflowVersion、TimelineRevision、DeliveryPackage、GenerationVariant 不可原地覆盖。
- `selection`、proxy winner、formal approval、episode approval、delivery 是不同语义。
- 所有状态写入通过 domain/application command；retry 只产生 JobAttempt，creative redraw 产生新 Variant + Job。
- 首版 LOCAL_ONLY、零公网出站；只允许本地进程、CLI、loopback HTTP；不提供远程 credential/API/费用/fallback。
- 模型、工作流、Profile、制作规格和 DeliveryTarget 必须由项目显式选择；无隐式默认。
- H3 仅为候选 Profile；严格服从 `model_manifest.json` 的 canonical root、禁用/禁止模型和 one-worker-one-task 约束。
- 不实施 G11 legacy 迁移；旧项目内容不进入 G0—G10 验收。

## 基线证据

- `00_总索引与文档治理.md`：权威顺序、术语、固定目录、LOCAL_ONLY 和 G0 门禁。
- `01_产品需求与验收范围.md`：86 条功能需求、14 条非功能需求和产品级完成定义。
- `09_实施WBS_阶段门禁与评审.md`：G0→G10 顺序、阶段输出、stop-the-line 条件。
- `10_测试策略_用例矩阵与发布检查.md`：85 个主干测试、规模基线和发布检查。
- `F:\AI_Projects\h3\model_manifest.json`：H3 本机候选、禁用资产、runtime 和历史证据；当前 Comfy 后端离线，故候选不可激活。

## 文档状态说明

蓝图原文的 DRAFT/BASELINED 状态由产品负责人按 00 的治理流程管理；本文件是本次执行任务的实现基线快照，不改写蓝图原文，不把 DRAFT 文档伪装成已签字规范。任何后续冲突必须先写 ADR、更新追踪和测试影响。

