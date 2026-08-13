# ADR-0010：项目显式 DeliveryTargetVersion

- 状态：Accepted for G0/G8 implementation baseline
- 需求：FR-PRJ-001、FR-DEL-003、FR-DEL-001、NFR-REL-003

交付画幅、fps、编码、码率/CRF、音频、响度、字幕、封面、QC 容差和包装规则必须由项目显式创建/选择并版本化。应用不注入交付默认。未选择目标时只能显示阻塞和“配置交付规格”；不同目标创建独立不可覆盖的 DeliveryPackage。

