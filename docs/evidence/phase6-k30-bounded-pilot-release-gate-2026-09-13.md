# Phase 6 / K30：受控小样发布闸门

日期：2026-09-13

## 结论

离线、真实隔离浏览器、真实本地 LLM、真实 ComfyUI/H3 与正式交付发布闸门已通过。整剧入口新增明确的所选分集范围：页面必须选择 1—2 集，后端对超过 2 集、重复 ID 和跨项目 ID 在准备/派发前拒绝。未选分集不会进入 readiness 修复，也不会建立生产 Run，因此不会把两集验收意外放大为 60 集 GPU 任务。

## 边界与事实

- `WholeDramaRunRequest.episode_ids` 支持受控小样范围，且 OpenAPI/生成客户端已同步。
- `WholeDramaOrchestratorService` 在准备和派发两个阶段应用同一范围；父命令幂等哈希包含所选分集，换范围不能复用旧 key。
- 两个所选集逐集隔离：第一集处于人工审核阻塞时，第二集仍可独立派发；结果真实返回 `PARTIALLY_DISPATCHED`、1 个 blocked、1 个 dispatched。
- 旧未传范围的 API 行为保留，供已授权的正式全剧调用兼容；当前一键页面只走 1—2 集受控入口。
- 单集入口、旧 Profile、已完成候选和已有运行均未迁移或删除。

## 发布闸门结果

- 后端组合：`test_whole_drama_orchestrator.py`、`test_episode_worker_actions.py`、`test_compose_duration_guard.py`、`test_comfy_workflow_bindings.py` 全部通过。覆盖所选范围、部分推进、幂等父命令、操作影响、时长防线、Comfy 参数绑定。
- 前端组合：`OneClickPipelineWorkbench.test.tsx`、`EpisodeProductionWorkspace.test.tsx`、`DirectorDeskPage.test.tsx` 共 37 项通过。
- Web 全量回归：138 个测试文件、578 项全部通过；生产构建和 bundle budget 通过。
- API 全量回归：242 个测试文件、1,423 项测试；1,422 项通过，1 项为 Windows 主机上预期跳过的 POSIX 文件权限合同，零失败。
- OpenAPI/客户端：`scripts/generate_client.py` 已重新生成 `docs/openapi/openapi.json` 与 `apps/web/src/generated/api.ts`。
- `git diff --check` 通过；本轮相关 Python 文件 Ruff 全部通过。

## 全仓基线审计说明

架构债务清单已用官方审计脚本刷新到当前 `HEAD` 的真实基线；本轮新增的 5 个具体 SQLite `Database` 类型依赖已改为 `DatabaseUnitOfWork` 端口，`EpisodeProductionRunService` 新增的两个跨服务构造也已改为应用层函数边界。架构清单专项与相关生产运行测试通过；全量结果见 `docs/evidence/final-full-regression-and-runtime-closure-2026-09-13.md`。

## 真实环境证据

- 本地 `qwen3.8:27b` 经 Ollama 实际返回预期中文短句，总墙钟约 29.4 秒。
- RTX 3090 Ti / ComfyUI 0.33.1 通过平台 Job 真实生成 H264 + AAC MP4。通过样本的 `prompt_id` 为 `e7f8261c-5766-4caf-bb41-be9b23d682b5`，Job 为 `868b57d9-d03e-47f0-a88e-8fad2f5f8bc9`，attempt 为 `2a0cccb7-455e-44b6-8173-f4a5150ab591`，输出 SHA256 为 `627662516ed86063e79f5869f39c7661199d9f318c1fb7889170c80e4d7c2b49`。
- 同一媒体已完成机器 QC PASS、人工 APPROVED、`FORMAL_SELECTION`，并进入真实 FFmpeg 合成、交付 manifest、交付核验与 HTTP 200 下载。
- 代表性三帧抽查显示同一雨夜旧屋主体和构图保持稳定；第一轮误把产品 UI 截图作为故事首帧的产物被人工否决，没有作为画质通过证据。
- 隔离浏览器两集小样只处理显式选择的 EPISODE_001/002；两集的人工关键帧阻塞分别保留。API 进程真实重启后页面仍恢复第二集 21 镜与相同确认状态。

没有自动下载/删除权重，没有修改生产数据库，也没有把单镜小样虚构成整部成片画质保证。
