# ADR-0005：ComfyUI Designer/Production 隔离与 H3 Worker 回收

- 状态：Accepted for G0/G6 implementation baseline
- 需求：FR-WFL-004、FR-PRV-001、FR-GEN-006、NFR-REL-002

## 决策

- Designer 仅用于工作流编辑、测试和 capture，写入独立 sandbox，不直接写正式项目目录。
- Production Comfy 由 Worker 按已发布 Workflow/Profile snapshot 启动，拥有独立 cwd、port、output/temp/user/log。
- H3 每个正式任务一任务一 Worker，任务结束后确认子进程退出并回收显存；残留或崩溃进入 reconciliation，不手工改成功状态。
- 不扫描整个 Comfy output 猜文件，只根据 prompt_id/history 和 output contract 收集。
- `model_manifest.json` 的 forbidden/disabled 模型不可进入 Active Profile；当前 First/Last 仅 EXPERIMENTAL。

