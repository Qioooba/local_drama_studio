# Phase 5 / K29：四种显式操作与只读影响预览

日期：2026-09-13

## 结论

单集工作台现在明确区分 `继续未完成`、`原样重试`、`新拍一个候选`、`仅重新合成`。选择操作时先调用只读影响预览，展示复用、等待、原样重试、新生成、依赖阻塞、人工确认与仅合成的精确集合；预览不写数据库、不建 Job、不调用网络或 GPU。

## 实现事实

- `EpisodeWorkerActionService.operation_impact` 复用当前 Job、候选、依赖和 stale facts 计算影响集合，并返回稳定 `plan_hash` 与 `gpu_video_job_count`。
- `RETRY_ORIGINAL` 指向失败 Job 的冻结输入，保留原 seed、variant、输入引用和任务语义。
- `NEW_TAKE` 表示创建新候选，导演台统一使用“新拍候选”文案，不再把它叫作“重新生成”。
- `RECOMPOSE_ONLY` 只检查当前时间线/合成依赖，预览中的 GPU 视频任务数固定为 0；缺时间线时明确阻塞。
- 语义编辑仍走既有版本化 prompt、镜头编辑与重规划命令；影响预览只解释后果，不替代后端命令。

## 验证

- `pytest test_episode_worker_actions.py test_compose_duration_guard.py`：覆盖失败任务原样重试、新候选计划哈希、仅合成零 GPU、缺时间线阻塞，以及实际依赖指纹。
- `vitest EpisodeProductionWorkspace.test.tsx`：19 项通过，覆盖四种选择和影响集合呈现。
- `vitest DirectorDeskPage.test.tsx`：12 项通过，验证新拍入口仍调用既有候选派生命令。
- 本轮未连接真实 ComfyUI/GPU；影响预览自身也保证 `mutated=false`、runtime/network 均为 false。

## 兼容与回滚

旧命令和已有候选均保留；新增 API 是只读端点，前端面板可单独回退。没有删除历史 Job、候选、计划或时间线。
