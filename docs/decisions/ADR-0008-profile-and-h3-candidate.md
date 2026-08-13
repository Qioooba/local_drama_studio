# ADR-0008：CapabilityProfileVersion 与 H3 候选导入

- 状态：Accepted for G0/G3/G6/G7 implementation baseline
- 需求：FR-PRV-001、FR-PRV-003、FR-GEN-006、FR-OPS-001、FR-OPS-002

## 决策

Runtime、ModelArtifact/Bundle、WorkflowVersion、CapabilityProfileVersion、ProductionPlanVersion、DeliveryTargetVersion 分层且版本化。Job 只引用冻结 snapshot，不能读取当前配置页面的最新值。Profile 的 input contract、seed support/determinism、director controls、post-process steps 和 output contract 是 UI/API 的唯一能力来源。

本机 H3 从 `F:\AI_Projects\h3\model_manifest.json` 只导入候选包；当前 manifest 标记为可验证候选的 T2V/I2V/Ref2V 可进入测试队列，First/Last 为实验，V2V 未验收，禁用/错误模型不得出现在 Active 列表。Comfy 后端当前离线，候选不自动激活。

