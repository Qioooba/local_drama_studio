# ADR-0008：托管式 llama.cpp 运行时与多态 GPU 生命周期适配器

- 状态：Accepted
- 日期：2026-08-29

## 背景

文本管线此前只有 Ollama 一个本地运行时。Qwen3.8-27B 的本机 GGUF 可由 llama.cpp 提供 Jinja chat template、JSON Schema 受约束解码与 MTP 投机解码；但 24 GB 单卡无法同时常驻文本权重和 ComfyUI 图像/视频权重，因此运行时切换必须有明确的进程与显存所有权边界。

`GpuRuntimeCoordinator` 的运行时处理是硬编码分支（`if runtime is GpuRuntime.OLLAMA ...`），每加一个运行时都要改 `prepare` 与 `cleanup` 两处。llama.cpp 的 `llama-server` 与 Ollama 不同：它由本应用拉起、显存只能靠进程退出归还，因此必须有一个"托管进程"型运行时，而不能以静态 `OPENAI_COMPAT` 指向一个常驻服务（切入 Comfy 时无法强制回收其显存）。

## 决策

1. **生命周期契约**：新增 `application/ports/gpu_lifecycle.py`，`GpuLifecycleAdapter` 协议包含 `runtime` / `activate()` / `evict(mode)`；`GpuLifecycleAdapterRegistry` 要求每个 `GpuRuntime` 成员恰有一个适配器，并固定跨运行时清除顺序。契约与现有静态 LOCAL_ONLY adapter 校验层（`infrastructure/adapters.py`）分层：后者只校验传输边界，不做进程操作。
2. **多态协调器**：`GpuRuntimeCoordinator.prepare` 变为"先按注册表顺序 `evict(SWITCH)` 所有其他运行时，再执行一次全局显存释放闸门，最后 `activate` 目标运行时"；`cleanup` 对被租约运行时 `evict(RELEASE)` 后再过同一闸门。显存闸门独立于 Comfy adapter，避免在 Ollama 尚未卸载时提前检查 VRAM。闸门优先读取 `nvidia-smi` 的设备级总显存/空闲显存并要求 ≥80% 空闲；仅在系统探针不可用时回退到 Comfy `system_stats`。这是因为在线验证确认 Comfy 遥测可能只反映本进程分配器，不能证明外部 llama-server 已释放 CUDA。保留 Comfy 队列非空拒绝抢占、Ollama `keep_alive=0` 与不可达容忍、同运行时排队保活及清理失败标 `DEGRADED`。
3. **托管进程管理器**：`infrastructure/llama_server_manager.py` 拉起、探活（`/health` 503 只表示加载中）、复用（spec 相同则不重启）、终止并等待 OS 级进程退出。Windows 终止覆盖完整进程树，避免启动器退出后真实 server 成为孤儿。pidfile 允许新管理器实例领养健康孤儿或回收别名不符的遗留进程。启动超时默认 180 s，端口占用判定带 2 s 有界等待。
4. **运行时映射**：`GpuRuntime` 新增 `LLAMA_CPP`；`SCRIPT_BREAKDOWN_LOCAL_LLM` / `LOCAL_LLM_PROBE(load_test=true)` 且 provider 为 `LLAMA_CPP_MANAGED` 的 loopback 作业映射到该运行时；V2 `MODEL_PLATFORM_EXECUTION` 快照可声明 `scheduler_runtime: "LLAMA_CPP"`。迁移 0087 放宽 `gpu_runtime_leases` / `gpu_runtime_state` 的 CHECK 约束。
5. **客户端**：`LocalLLMClient` 新增 `LLAMA_CPP_MANAGED` provider——loopback 专用传输、拒绝 API Key、chat-completions 传输上附加 `response_format: json_schema`（无 schema 时 `json_object`）与 `chat_template_kwargs: {"enable_thinking": false}`（对应 Ollama `think:false` 合同）。上下文窗口在启动期由 `-c` 固定，per-call `num_ctx` 不转发。`llm_base_url` 由 `llama_server_host/port` 派生、模型别名缺省取 GGUF 文件名，保证客户端与子进程指向同一端点。
6. **配置面**：`Settings`、机器配置 `RuntimeConfig` 与环境变量三层一致。MTP 使用类型化的 `llama_mtp_enabled` / `llama_mtp_draft_tokens`，由管理器生成 `--spec-type draft-mtp --spec-draft-n-max N`；`llama_server_args` 只能承载管理器未拥有的可选参数。`ollama_base_url` 是独立生命周期端点，不能随当前默认推理 Provider 一起被改写。

## 不变量

- 同一时刻至多一个本机 GPU runtime lease 有效；LLAMA_CPP 与 OLLAMA/COMFY/PYTORCH 互斥。
- 切入任何运行时前，所有其他 runtime 必须完成驱逐；显存闸门只能在完整驱逐序列之后、目标激活之前执行。
- 外部进程占用的释放判定必须来自设备级探针；运行时自身的分配器遥测只可作为系统探针不可用时的降级来源。
- llama-server 显存归还的权威边界是进程退出；`evict(RELEASE)` 返回前进程必须已退出。
- 托管端点只能是 loopback，不携带凭据；远程 OPENAI_COMPAT 不经过协调器生命周期。
- 相同运行时的排队 Job 保留 llama-server 常驻；无同类任务时立即终止。
- 启动崩溃、端口被占、超时都把 runtime 标为 `DEGRADED` 并给出日志路径与建议动作，不得悬挂租约。

## 结果

文本阶段可以在 Ollama 与托管 llama.cpp 之间以 Profile 切换做 A/B，无需让当前默认 Provider 决定另一个 runtime 是否可管理。切换路径多态化后，新增 runtime 通过 adapter 注册完成。已知取舍：进程托管会产生冷启动成本，由同运行时排队保活摊薄；升级 llama.cpp 构建时必须重新执行 MTP 与结构化输出验证。

## 附注：Model Platform V2 供给链路（2026-08-29 补充）

托管 llama.cpp 运行时按 Ollama 同构的方式接入 V2 平台，全链路均为显式实现而非通用模板：

- **发现**：`GgufDirectoryRuntimeAdapter`（`gguf_discovery.py`）只读扫描操作员配置目录中的 `*.gguf`，解析 `general.*` 元数据并计算完整 sha256。CLIP/mmproj 不作为文本候选。`LlamaCppDiscoveryOrchestrator` 冻结派生 loopback 端点、上下文、GPU 层、KV、MTP、额外参数摘要与模型根摘要，建立 RuntimeVersion（DRAFT）；API 入口为 `POST /discovery-runs:llama-cpp`。
- **登记与冒烟**：`DiscoveryRegistrationService` 以 `GGUF` format 登记候选；`CapabilitySmokeService` 在 `GPU:0:EXCLUSIVE` 租约内按已登记的 `native_locator` 拉起对应 GGUF，探完释放，不能退回全局默认模型。
- **Profile**：`LlamaCppTextProfileService`（模板 `llama_cpp.text.profile.v1`，adapter `llama.chat.v1`）与 Ollama 模板同构，但参数契约刻意**不含 `num_ctx`**（上下文窗口在启动期由 `-c` 固定）；资源策略冻结 `gpu_runtime: "LLAMA_CPP"`。
- **执行**：`llama_cpp_text_execution.py`（handler `llama.text.v2`）校验冻结快照的网络策略与端点身份后，经 `LLAMA_CPP_MANAGED` provider 执行；提交侧由 `ExecutionHandlerDescriptor(gpu_runtime="LLAMA_CPP")` 冻结调度运行时，复用 ADR-0008 的单卡租约与生命周期适配器。
