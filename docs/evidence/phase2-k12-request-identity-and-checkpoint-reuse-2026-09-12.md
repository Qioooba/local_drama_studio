# Phase 2 / K12：请求身份、草案与检查点复用一致

日期：2026-09-12\
范围：K12\
环境：隔离 pytest SQLite、fake LLM transport；未访问真实远程 LLM、GPU、生产数据库或在线服务。

## 结论

K12 已完成。分层分析、单集拆解和一键故事规划的复用条件都绑定到实际消费输入；重试使用首次冻结请求，不再因知识索引或后续结果变化而漂移。调用计数由真正的 `chat_json` 调用产生，不再使用“集数 + 1”估算。

## 实现证据

- `adaptation_analysis_execution.py` / `adaptation_plan_repository.py`
  - 第一次执行节点时原子保存 `adaptation-analysis-request/v2` 检查点，包含最终 system prompt、最终 user input（含当时同源检索片段）、推理配置和运行时合同。
  - `request_sha256` 对完整冻结 envelope 计算；每次真实尝试仍记录独立 `llm_invocations`，重试共享请求内容但不抹掉调用事实。
  - Profile provider/model/base URL/connection 与 Job 冻结值不一致时阻断。
  - 下游节点只读取上游 `planning_state + result`，不会把上游 request checkpoint、原文或提示再次塞入 Reduce。
  - 没有 usage 数据时 `input_tokens/output_tokens` 保持 NULL，不伪造 0。
- `story_pipeline_ai.py`
  - 每集检查点身份覆盖 prompt 合同、集号、code、段落范围、实际 24k 有界 prompt、视觉风格、目标时长与推理配置。
  - 来源、范围或提示条件变化时不复用旧集提纲。
  - `llm_call_count` 由计数代理包裹真实 transport；包括结构修复和拆分综合调用，排除复用检查点。
  - 新增 `reused_episode_checkpoint_count` 明确区分复用与调用。
- `local_llm.py` / `episode_preparation.py`
  - 拆解 Job v4 生成完整 `request_identity_sha256` 并在执行时校验自身完整性；最终草案证据保留该身份。
  - EpisodePreparation 只复用同时匹配 episode、source version、committed import、段落范围、目标时长、Profile 与新版 request identity 的草案。

## 核心断言

- 分层分析数据库中的 `request_sha256` 等于实际冻结 envelope 的 SHA-256。
- fake transport 调用数等于 `llm_invocations` 行数，prompt contract 为 `adaptation-analysis/v2`。
- 冻结后提供不同检索/提示候选，旧节点仍返回原检查点，漂移内容不进入请求。
- 所有下游 user prompt 均不含 `request_checkpoint`。
- 精确集检查点复用时只发生 1 次综合调用；更换原文后发生“1 次集提纲 + 1 次综合”，不误命中。
- 一次集提纲结构修复，加一次失败综合和四个组件综合，fake transport 与元数据都精确为 7 次。
- 错误范围和错误 Profile 的 DRAFT_READY 草案不会被 EpisodePreparation 自动应用，而是创建新的正确 Job。

## 回归结果

```text
.venv/Scripts/python.exe -m pytest -q \
  apps/api/tests/test_adaptation_plans.py \
  apps/api/tests/test_story_pipeline_ai.py \
  apps/api/tests/test_pipeline_orchestrator.py \
  apps/api/tests/test_episode_preparation.py \
  apps/api/tests/test_ai_breakdown_drafts.py \
  apps/api/tests/test_breakdown_apply.py
```

结果：103 个收集用例全部通过。Ruff 与 `git diff --check` 通过；仅有既存 Starlette/httpx、Alembic 弃用警告和 Git LF/CRLF 提示。

## 未扩大声明

- 没有新增计费/观测平台或存储密钥、认证头。
- fake transport 只证明身份、次数和恢复语义，不代表真实模型质量或 token usage 已验收。
- K13 的质量门禁与应用影响仍由下一工作包完成。
