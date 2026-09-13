# Phase 2 / K09：分集不可变原稿绑定证据

- 日期：2026-09-12（UTC+08:00）
- 基线提交：`e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3`
- 范围：分集计划写入、准备、重规划、前半段只读检查、草稿资格
- 隔离：pytest 临时 SQLite 与临时项目目录；未访问生产库、在线 API、真实模型、GPU 或远端网络

## 复现结论

旧实现会在准备、重规划和前半段检查中按项目时间顺序隐式选择最近导入；只读草稿查询也可能把同项目其他来源或其他分集的 `DRAFT_READY` 当作当前分集待办。项目导入 B 后，已按 A 规划的分集存在串稿风险。

## 最小改造

1. 新增统一的分集源解析器，绑定并校验 `source_document_version_id`、`import_session_id`、提取文本 SHA-256 与段落范围。
2. 新规划写入分集时持久化完整源绑定；项目后续导入不自动改写既有分集绑定。
3. 旧数据只接受唯一的已应用拆解证据，或唯一的已提交导入；多候选时返回 `EPISODE_SOURCE_BINDING_AMBIGUOUS`，不猜最新版本。
4. 准备、重规划请求/预览/应用及前半段快照统一使用同一绑定，并在新源派生工作前验证受控提取文本的实际 SHA-256。
5. `DRAFT_READY` 与已应用拆解均限定目标分集、源版本、导入会话和范围；不相关草稿不进入指纹和待办。

## 验收断言

- A/B 段号相同、人物不同：导入 B 后，A 分集准备与重规划仍提交 A 的导入会话。
- B 的同集旧草稿不出现在 A 的准备或前半段检查中。
- 提取文本被改写后，准备以 `EPISODE_SOURCE_TEXT_CHANGED` 停止。
- 跨项目源版本与不存在的源版本均以 `EPISODE_SOURCE_VERSION_NOT_FOUND` 停止。
- 旧分集只有一份提交源时可恢复绑定与提交范围；两份候选源时停止并要求明确绑定。
- 新规划应用后，分集 `source_range_json` 含版本、导入会话、文本 SHA 与段落范围。

## 分层回归

执行：

```powershell
.\.venv\Scripts\python.exe -m ruff check apps/api/local_drama/application/episode_source_binding.py apps/api/local_drama/application/episode_front_half_actions.py apps/api/local_drama/application/episode_preparation.py apps/api/local_drama/application/episode_replan.py apps/api/local_drama/application/pipeline_orchestrator.py apps/api/tests/test_episode_source_binding.py apps/api/tests/test_episode_replan.py apps/api/tests/test_episode_production_runs.py apps/api/tests/test_pipeline_orchestrator.py
.\.venv\Scripts\python.exe -m pytest -q apps/api/tests/test_episode_source_binding.py apps/api/tests/test_episode_preparation.py apps/api/tests/test_episode_replan.py apps/api/tests/test_pipeline_orchestrator.py apps/api/tests/test_episode_production_runs.py
git diff --check
```

结果：Ruff 通过；35 项 pytest 通过；`git diff --check` 通过。仅有既存 Starlette/Alembic 弃用警告和 Git 行尾提示，无测试失败。
