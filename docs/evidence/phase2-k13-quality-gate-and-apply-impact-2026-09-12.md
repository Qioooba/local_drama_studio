# Phase 2 / K13：统一质量门禁与应用影响预览

日期：2026-09-12\
范围：K13\
环境：隔离 pytest SQLite、Vitest/jsdom；未访问真实模型、GPU、生产数据库或在线服务。

## 结论

K13 已完成。故事规划质量由 `pipeline-quality/v2` 单一规则表派生 checks、blockers、warnings 和 status；应用时重新计算，不信任历史绿色字段。应用前必须取得 `pipeline-apply-impact/v1` 只读预览并携带预览哈希，事务内事实或 revision 变化会拒绝写入。

## 实现与行为

- 质量检查显式记录 `severity`、`applicable`、`passed`。
- 当前产品合同中的原稿冻结、模型生成、分集存在、核心人物、纯文本生成和无生产写入为阻断规则；原稿完整覆盖与核心场景为空为警告规则。
- 旧质量规则不能冒充通过；当前原稿版本、数据库 hash 与提取文件 hash 均在预览/应用重新验证。
- 影响预览返回：
  - 分集 `add/update/preserve/skip`；
  - 总纲创建或当前 revision 指针切换；
  - 核心资产 `reuse/add`；
  - 已制作分集不被重写但后续上下文会变化的准确集合；
  - `requires_confirmation` 与稳定 `impact_sha256`。
- 已有镜头的分集保持标题、来源范围和镜头不变；无镜头的空分集可更新；预览本身 `writes_performed=false`。
- 前端先展示影响。存在已制作内容/总纲切换时必须再次确认；“取消预览”不调用应用 API。无冲突且质量 READY 的首次一键流程可在预览后自动继续。

## 关键反例

- 将核心人物从草案中移除、但保留历史质量 JSON：预览重算为 BLOCKED，应用返回 `PIPELINE_QUALITY_BLOCKED`，正式表计数不变。
- 原稿数据库 hash 在预览后变化：应用返回 `PIPELINE_SOURCE_CHANGED`，无业务写入。
- 错误 revision：预览返回 `PIPELINE_REVISION_CONFLICT`。
- 错误 impact hash：应用返回 `PIPELINE_APPLY_IMPACT_CONFLICT`。
- 已制作第 1 集 + 新第 2 集：预览准确报告 EPISODE_001 preserve、EPISODE_002 add、既有角色 reuse、新道具 add，以及 EPISODE_001 上下文变化；前后数据库计数相同。
- UI 取消影响预览后 `applyPipelineRun` 调用次数仍为 0；再次预览并确认后才调用一次。

## 回归结果

- 后端 K13 主套件：49 个收集用例全部通过。
- `test_pipeline_orchestrator.py`：11 项通过。
- `OneClickPipelineWorkbench.test.tsx`：6 项通过。
- Ruff 与 `git diff --check` 通过。
- OpenAPI 已同步，包含 `previewPipelineApply` 与必填 `expected_impact_sha256`。
- `npx tsc --noEmit` 仍只有任务开始前已记录的无关错误：`apps/web/src/features/profiles/LocalLLMConfigurationPanel.tsx:181` 将可选 `preset.model` 传给非可选 state setter；本包未新增类型错误。

## 未扩大声明

- 质量规则只验证当前故事产品合同，不扩展为艺术评分器。
- 预览不批准媒体、不建立 canonical 图、不改变人工内容。
- 真实浏览器与真实模型验收留在 Phase 6。
