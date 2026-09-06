# HANDOFF 2026-08-30：托管式 llama.cpp 运行时

> 决策记录：[ADR-0008](./decisions/ADR-0008-managed-llama-cpp-runtime.md)  
> 真机证据：[managed-llama-runtime-validation-20260830.json](./evidence/managed-llama-runtime-validation-20260830.json)

## 结论

托管 llama.cpp 已接入单卡 GPU 租约、Model Platform V2 和本地 LLM 配置界面。实现不是在原协调器继续追加运行时分支，而是把运行时生命周期重构为 adapter 注册表，并把显存释放检查提升为“所有旧运行时驱逐完成后”的全局闸门。闸门以 `nvidia-smi` 的设备级显存为主数据源；只有系统探针不可用时才回退到 Comfy 遥测。

当前机器已用 RTX 3090 Ti、官方 llama.cpp `b10689` 和现有 Ollama Qwen3.8 27B Q4_K_M 模型层完成真实验证：普通模式、MTP、JSON Schema、独占租约、显存回收、在线 llama→Comfy 切换，以及 GGUF 发现→登记→四能力冒烟→Profile 冒烟/发布均通过。`config/config.json` 仍以 Ollama 为默认 Provider，没有强制切换生产默认值。

## 关键契约

- `GpuRuntimeCoordinator` 先驱逐所有非目标 runtime，再过一次显存闸门，最后激活目标；清理时先停止目标，再过闸门。设备级探针需达到 ≥80% 空闲显存才放行。
- `llama-server` 由 `LlamaServerManager` 所有。`/health` 503 是“仍在加载”，只有 200 才可服务；停止在 Windows 上覆盖完整进程树。
- Profile 的 GGUF `native_locator` 会进入 activation context；能力冒烟、Profile 冒烟和 worker 执行加载该 Profile 绑定的文件，不能静默退回全局默认 GGUF。
- RuntimeVersion 冻结非秘密启动策略：端点、上下文、GPU 层、Flash Attention、KV、MTP，以及额外参数和模型根的摘要。机器策略变化后，旧 Profile 必须重新扫描、验证和发布。
- Ollama 生命周期端点使用独立 `ollama_base_url`。当前推理 Provider 即使是 `LLAMA_CPP_MANAGED` 或远程 OpenAI 兼容服务，也仍能回收本机 Ollama 模型。
- `LLAMA_CPP_MANAGED` 仅允许本机 loopback、拒绝 API Key，并使用 OpenAI chat-completions 的 `response_format: json_schema`；每次请求不覆盖启动期上下文窗口。

## 配置

机器配置 `runtime`、`Settings` 和 `LOCAL_DRAMA_*` 环境变量保持同构：

```text
ollama_base_url                  默认 http://127.0.0.1:11434（生命周期端点）
llama_server_bin                 llama-server.exe
llama_model_path                 默认 GGUF；同时决定 V2 扫描根目录
llama_server_host / port         默认 127.0.0.1:8101
llama_ctx_size                   默认 8192
llama_gpu_layers                 默认 99
llama_flash_attn                 on | off | auto
llama_kv_cache_type              默认 q8_0
llama_mtp_enabled                默认 false
llama_mtp_draft_tokens           默认 2
llama_server_args                只允许非生命周期可选参数
llama_startup_timeout_seconds    默认 180
```

MTP 由类型化配置生成：

```text
--spec-type draft-mtp --spec-draft-n-max 2
```

模型、端点、上下文、GPU 层、Jinja、KV 和 MTP 参数由管理器拥有；在 `llama_server_args` 中重复它们会直接拒绝启动配置。

## 本机真机结果

- llama.cpp：`0.3.0-dev`，build `10689`，commit `57291f264`，Windows CUDA 12.4 官方包且 SHA-256 已核验。
- 模型：复用 Ollama blob 的 NTFS 硬链接 `F:/AI_Models/LocalDramaStudio/Ollama/gguf/Qwen3.8-27B-Q4_K_M.gguf`，16,810,714,464 bytes，GGUF v3。
- 普通模式：冷启动 25.266 s；加载后显存 17,775 MiB；受约束 JSON 推理 1.125 s；停止后 1,554 MiB。
- MTP：冷启动 28.812 s；加载后显存 19,382 MiB；受约束 JSON 推理 1.078 s；停止后 1,542 MiB。
- 协调器：独占租约进入 `READY/LLAMA_CPP`，心跳续租，推理后以 `COMPLETED` 释放并回到 `IDLE`。
- Model Platform：完整文件发现 63.469 s；四个文本能力均 `SMOKE_PASSED`；安装 `READY`、RuntimeVersion `ACTIVE`；Profile smoke 通过并发布为 `PUBLISHED`。
- 在线 llama→Comfy：llama 驻留时显存 18,141 MiB，设备级闸门保持阻塞；切换并结束 llama 进程树后 4.438 s 放行，Comfy 会话内显存 1,767 MiB，结束后协调器 `IDLE` 且无活动租约。

以上单次短输出数字只证明功能与资源边界，不构成 MTP 性能结论；正式 A/B 应使用相同 prompt、输出长度和多次采样。

## 验证与边界

自动化覆盖进程复用、503 加载态、崩溃/超时、端口冲突、孤儿领养/回收、Windows 进程树停止、MTP 命令、模型路径约束、GPU 驱逐顺序、RuntimeVersion 过期、V2 Profile 绑定和前端托管身份。前端配置面板测试与生产构建通过。

在线验证发现 ComfyUI `system_stats` 在 llama.cpp 占用约 16 GiB CUDA 显存时仍报告几乎全空闲，说明该值在当前 Windows/Comfy 组合中是进程分配器局部视角，不能独立证明其他进程已释放显存。实现因此改为优先读取 `nvidia-smi` 的设备级总量/空闲量，Comfy 遥测仅在系统探针不可用时兜底；真实切换已证明 llama 驻留时不误放行、进程树退出后才放行。Comfy 队列非空拒绝抢占和 `/free` 仍保持原契约。

## 运行时资产

- llama.cpp：`E:/Tools/llama.cpp/b10689/`
- 下载缓存：`E:/Tools/llama.cpp/downloads/`
- 可发现 GGUF：`F:/AI_Models/LocalDramaStudio/Ollama/gguf/Qwen3.8-27B-Q4_K_M.gguf`
- 真机临时数据库与日志：`work/llama-real-validation/`

生产切换仍应通过机器配置或部署环境变量完成，不应把上述本机绝对路径写进代码。
