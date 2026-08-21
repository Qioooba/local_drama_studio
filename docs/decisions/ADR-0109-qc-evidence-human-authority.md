# ADR-0109：机器 QC 只产生证据，人工审核拥有批准权

- 状态：Accepted
- 日期：2026-08-20
- 对应：xinjihua 02 §80 ADR-009、§81；03 P7/P9/P10

## Context

机器质量检查可发现技术缺陷并触发有界自动重抽，但生成完成、候选选择、正式批准和可交付是不同事实。若机器 QC 创建 human approval 或删除失败候选，会破坏审计与人工权威。

## Decision

machine check run/result 只记录版本化证据和 disposition。QC policy 按 project/episode/shot 解析 auto-reroll 上限与允许类别；自动动作只追加 variant/job/audit/outbox。候选不因失败或重抽删除。selection 与 formal review/approval 继续由既有人工命令显式提交，机器 QC 永不创建 human approval。

## Rejected alternatives

- QC PASS 自动批准：拒绝，因为技术阈值不能替代创作和交付决策。
- 前端硬编码阈值与重试次数：拒绝，因为 Worker 和 UI 会分叉。
- 重抽覆盖/删除旧候选：拒绝，因为 lineage、输入和问题证据会丢失。

## Consequences

自动修复可控且可解释，人工权威单一；达到上限后必须显示“需要人工处理”。新 QC 类别必须明确是否允许自动动作，并通过 policy resolver 而非临时代码。

## 事实来源

`0045_qc_policies.py`、`qc_policy_repository.py`、`test_qc_auto_reroll_policy.py`、`test_formal_video_qc.py`、`test_formal_selection_commit.py`、`test_refactor_fact_guardrails.py`。
