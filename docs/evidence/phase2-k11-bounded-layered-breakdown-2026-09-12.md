# Phase 2 / K11：复用分层分析与有界本集拆解

日期：2026-09-12\
范围：K11；同时修补 K09 在改编计划物化入口的来源绑定缺口。\
环境：隔离 pytest SQLite 与临时项目目录；未访问真实远程 LLM，未调用 GPU，未修改生产数据库或在线服务。

## 结论

K11 已完成。短范围保留原单次拆解路径；长范围按实际发送的编号文本切成不超过 4,000 字符的可恢复子任务。子结果以内部 `CHUNK_READY` 检查点保存，按冻结的 segment ordinal 聚合为一个正式草案，并且只由既有 `BreakdownApplyService` 做一次最终应用。

已有 `CHUNK_MAP` 分析没有被替换或另建 DAG。拆解任务只选择同项目、同原稿版本、与当前范围相交的最新成功分析节点，并在入队时冻结节点引用、输出哈希、有界摘要及摘要哈希。后续分析变化不会改变旧任务重试的实际上下文。

## 实现证据

- `apps/api/local_drama/application/local_llm.py`
  - `_bounded_numbered_source_segments` 以最终 `[Pnnn]` 文本计量；长单段优先按句界切分，无法按句切时按字符切分，并保留精确 Unicode offset。
  - Job v4 冻结所有 segment 的范围、原段落号、输入字符数和 SHA-256。
  - `_freeze_breakdown_analysis_contexts` 复用同源、相交的成功 `CHUNK_MAP`；每段分析上下文限制为 1,200 字符。
  - `_segmented_breakdown` 使用稳定检查点 ID；显式重试复用成功段，只重跑缺失段。
  - 聚合按 segment ordinal 与场景顺序重编号；原文证据同步映射到新的全局 `scene_no`，避免场景与证据错位。
  - 最终综合仅做确定性结构合并和时长归一化，不再把全部分块原文发送给一次无界 LLM 综合。
  - `CHUNK_READY` 不出现在用户可见草案列表。
- `apps/api/local_drama/infrastructure/database/adaptation_plan_repository.py`
  - 改编规划物化要求存在已提交导入会话。
  - 新 Episode 的 `source_range_json` 保存明确 source version、import session、text hash、段落范围与原 Unicode span 证据；不再写无法被 K09 消费的裸数组。

## 验收覆盖

- 实际编号输入 3,999 / 4,000 / 4,001 字符边界。
- 超过 5,001 字符的单段精确重建；切分 offset 连续、无丢失、无重叠，边界对白只出现一次。
- 61 段以上范围不再准备时无解，所有模型子输入均不超过 4,000 字符。
- 中段失败后显式重试：首段检查点复用，只再调用失败后的段；最终仅一个正式草案，尾段引用可追溯。
- 乱序完成：预先保存第二段，再执行第一段；最终仍按来源顺序合并。
- 自动应用：乱序聚合后只产生一次 `SCRIPT_BREAKDOWN_APPLIED`，目标 Episode 正好创建 2 场、2 镜。
- 分层分析：任务冻结后即使节点输出改变，实际 prompt 仍使用入队时冻结的同源摘要，不漂移到新结果。
- 改编物化后的 Episode 可由 `resolve_episode_source_binding` 直接解析到已提交原稿与有效段落范围。

## 回归结果

命令：

```text
.venv/Scripts/python.exe -m pytest -q \
  apps/api/tests/test_ai_breakdown_drafts.py \
  apps/api/tests/test_breakdown_apply.py \
  apps/api/tests/test_episode_source_binding.py \
  apps/api/tests/test_episode_preparation.py \
  apps/api/tests/test_episode_replan.py \
  apps/api/tests/test_adaptation_plans.py \
  apps/api/tests/test_pipeline_orchestrator.py
```

结果：82 个收集用例全部通过。仅有既存 Starlette/httpx 与 Alembic 弃用警告。

静态与补丁检查：

```text
.venv/Scripts/python.exe -m ruff check \
  apps/api/local_drama/application/local_llm.py \
  apps/api/local_drama/infrastructure/database/adaptation_plan_repository.py \
  apps/api/tests/test_ai_breakdown_drafts.py \
  apps/api/tests/test_adaptation_plans.py
git diff --check
```

结果：Ruff 通过；`git diff --check` 通过。Git 仅报告工作区既有的 LF/CRLF 转换提示。

## 未扩大声明

- 本包使用 fake transport 验证调用次数、输入和恢复语义；没有把真实本地模型质量或吞吐冒充为已验收。
- 没有新增章节 DAG、RAG、内存并发队列或正式 Episode 分块实体。
- K12 的完整 invocation/request identity 与调用计数仍是下一工作包，不在本证据中提前宣称完成。
