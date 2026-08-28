# ADR-0007：单卡 GPU 运行时编排

- 状态：Accepted
- 日期：2026-08-28

## 背景

本机只有一张 CUDA 显卡。ComfyUI 与本机 Ollama 是独立进程，都会缓存模型；任务的 worker channel 只表示由哪个 worker 执行，不能代表实际硬件消耗。原实现只互斥 `GPU_H3` channel，而 Ollama 小说拆解在 `CPU` channel 上，因此两者可以同时被 claim。任务结束也没有由模型所属进程执行卸载。

## 决策

采用三层模型：

1. `job_resources` 根据不可变 Job 快照判定真实 GPU runtime。Comfy 和 loopback Ollama 都映射到同一个 `GPU_H3_HEAVY` scheduler resource；私网另一台主机上的 Ollama 不占用本机 GPU。
2. `GpuRuntimeCoordinator` 通过 `gpu_runtime_leases` 在 API、worker 等进程间独占 `GPU:0:EXCLUSIVE`，后台 heartbeat 续租，过期租约可恢复。
3. 模型卸载必须由所属进程完成。切入 Ollama 前确认 Comfy 队列为空，调用 `/free` 并轮询显存；切入 Comfy 前通过 Ollama `/api/ps` 枚举并以 `keep_alive=0` 卸载模型。

## 不变量

- 同一时刻至多一个本机 GPU runtime lease 有效。
- runtime 切换在模型推理或 Comfy prompt 提交之前完成。
- Comfy 有外部运行/排队 prompt 时不得抢占。
- 卸载命令成功不等于显存已释放；必须复验 runtime 状态或显存。
- 后处理清理失败不得推翻已经持久化的成功产物，但必须把 runtime 标为 `DEGRADED`；下一次切换仍执行严格清理。
- 相同 runtime 的排队 Job 可以保留模型形成批处理；没有同类任务时立即卸载。

## 结果

小说拆解与视频生成不会再因 channel 名称不同而并发抢占单卡。模型切换增加少量卸载/复验时间，但避免 OOM、隐式 CPU offload 与不可预测的吞吐下降。多 GPU 扩展时可把资源键从固定 `GPU:0:EXCLUSIVE` 演进为设备分配结果，无需改变 runtime adapter 协议。
