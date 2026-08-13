# G7 进度报告（2026-08-14）

状态：`IN_PROGRESS`。G7-01—G7-09 当前闭环证据已落库；G7-10 模型 license 证据仍是硬阻塞，不能进入 G8 退出验收，也不能宣告 G7 PASS。

## 已闭环

- Migration `0016_g7_capability_compatibility`：Profile input slots、seed/determinism、输出媒体、GPU 并发，以及 extend/V2V/reference/motion matrix；生产 Published v12 `f63b3ac7-e264-4d44-adcc-eaf525aa1c34` 携带 PASS attestation `da5d5f66-daeb-4567-9bca-13335c7d95ed`。
- Migration `0017_g7_zero_public_network_e2e`：真实 loopback HTTP 服务驱动 Comfy/LLM/diagnostics client；socket guard 记录本地连接，`203.0.113.1` 在 connect 前被拒绝。生产 attestation `51b05922-fce9-496e-bb9e-a627ba346f77`。
- Migration `0018_g7_workspace_asset_authorization`：生产 KEYFRAME `0d389e44-0fc3-47e2-9edd-ee587ffdedf3` 重新计算内容 hash/size 后获得 `AUTHORIZED`；授权 `853fd791-0bc8-4cf5-bf12-fc54c3542caa`；BrandKit ACTIVE v1 `5a0d3a95-1aea-4482-a137-2a85439c8373`。
- Profile Editor 真 UAT：1440×900 只做一次 DRAFT→validate，1280×800/1024×768 只读；三档零溢出、零 console/page error、零失败响应、零原片请求；PASS 8/8，缩略图为 720px WebP。

## 当前硬阻塞

- Migration `0019_g7_model_compatibility_reports` 支持离线完整 SHA-256、大小、safetensors header tensor/dtype 与量化声明对照；不启动 ComfyUI、不加载模型、不联网。
- H3 video VAE 报告 `d586967e-0634-4c8d-8887-193d398beda9`：SHA-256 `5a624684fad53d4acd0762aa7b07de4204de0bbb90f92c479605e326ccceb148`，5,207,806,104 bytes，560 tensors，F16 header 与 FP16 声明匹配。
- 报告状态 `BLOCKED`：没有可审计的本地许可证记录（`LICENSE_EVIDENCE_MISSING`）。manifest 说明和文件存在性不能冒充 license；补入真实 operator license record 后才可推进 readiness。

## 回归

- `pnpm api:test:safe`：97 passed / 4 Comfy live deselected。
- `pnpm web:test`：9/9；`pnpm web:build`：TypeScript/Vite PASS。
- Ruff PASS；mypy PASS（81 source files）。
- 生产迁移 `0016→0019` 完成，在线备份与 `integrity_check=ok`；API 3210、Web 5173，ComfyUI 8188 无监听。

## 下一步

补入真实本地 license 证据后复跑报告；只有 G7 所有 blocker PASS 才能建立 G7 exit report 并进入 G8。最终目标仍需 86/86 FR、14/14 NFR、85 主干测试、完整本地 UAT、无 P0/P1、发布证据、安装/升级/回滚、SBOM 与 go/no-go。
