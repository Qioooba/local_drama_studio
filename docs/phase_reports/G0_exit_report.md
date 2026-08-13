# G0 阶段退出报告

- 基线：`local_drama_studio` 初始化；蓝图文档哈希见 `docs/evidence/g0/baseline-check.txt`
- app/schema version：未创建业务 schema；G1 负责工程骨架，G2 负责初始 migration
- 完成需求：G0-01—G0-10；建立 FR 86、NFR 14、TC 85 的基线登记
- 排除范围：11 号 legacy 迁移；云 Provider/API Key/计费/多租户；ComfyUI fork；完整 NLE

## 交付物

- 架构基线：`docs/decisions/ADR-0001-architecture-baseline.md`
- G0 相关决策：ADR-0002、0003、0004、0005、0006、0008、0010、0011
- 风险登记册：`docs/risk-register.md`
- 需求追踪初始化：`docs/requirements-traceability.md`
- ADR/缺陷/阶段报告模板：`docs/templates/`
- 只读验证脚本：`scripts/g0_validate.py`
- 文档与本机 manifest 证据：`docs/evidence/g0/baseline-check.txt`

## 入口评审结论

- 文档 00—10、12—17 已完整阅读；11 未纳入读取/实施路径。
- 计数验证：86 FR、14 NFR、85 主干 TC。
- 核心实体、状态机、目录/DB 权威边界、API 语义、人工硬闸门、Designer/Production 隔离、LOCAL_ONLY、60 集规模和 G11 延期均已登记。
- 本机 Python 3.12.10、Node 22.23.2、pnpm 9.15.9、FFmpeg 8.1.2 可用。
- H3 manifest 已检查：one-worker-one-task；T2V/I2V/Ref2V 为候选证据；First/Last 为实验；禁用资产 1 个；Comfy 后端当前离线，不能激活 Profile。

## 风险与待办

- G1 必须先建立可重复启动/停止、OpenAPI/client、静态检查和测试骨架。
- G2 必须先完成真实 SQLite migration、事务/不变量和备份 preflight，不能用 `create_all` 替代。
- G6 之前不得宣称真实 H3 生产闭环；manifest 中历史样片不代替新 Job 的 artifact/run manifest。
- 任何意外公网请求、路径逃逸、媒体覆盖/丢失、批准绕过、假成功或不可恢复 migration 触发 stop-the-line。

## 回滚

G0 仅新增基线文档和只读验证脚本，无业务数据库迁移；删除/回退本阶段只需移除新增 `local_drama_studio/docs` 和 `scripts/g0_validate.py`，不会触碰既有项目、样片、模型或旧脚本。

## 门禁结论

`PASS — G0 implementation baseline accepted under the user's explicit execution directive.`

源蓝图 DRAFT 状态不被本报告伪改；本地 baseline 记录了本次执行采用的范围。重大产品冲突仍需 ADR/用户确认；普通实现问题继续自行调查和修复。

