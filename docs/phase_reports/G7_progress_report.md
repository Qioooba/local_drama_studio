# G7 进度报告（2026-08-14 续跑）

状态：`IN_PROGRESS`。G7-01—G7-09 当前闭环证据已落库；G7-10 模型 license 证据仍是硬阻塞，不能进入 G8 退出验收，也不能宣告 G7 PASS。

## 本轮完成

- Migration `0020_g7_model_license_evidence`：新增项目内本地 license evidence 导入；只接受不可越界、非 symlink、UTF-8 JSON 记录，并强制声明当前模型 SHA-256 与 license 名称。支持 `LOCAL_LICENSE_VERIFIED`/`USER_OWNED`，不下载、不联网、不猜测许可证；旧报告不原地改写。
- 新 API：`POST /api/v1/projects/{project_id}/model-license-evidence`，导入后自动生成新的 hash/header/量化报告；不具备真实证据时仍保持 BLOCKED。
- 新回归：license evidence 正常闭环、SHA 不匹配、项目越界路径共 3 项；全 API 回归已升至 `100 passed / 4 deselected`。
- Playwright Profile Editor 三档已修正为确定性流程：1440×900 从 Published v12 派生一次新 DRAFT v15 后验证，1280×800/1024×768 只读；全部无水平溢出、console/page error、失败响应、原片请求。视觉复核只读取 720px WebP 缩略图，结果记录于 `docs/evidence/g7/profile-editor-visual-review-2026-08-14.json`。

## 已闭环

- Migration `0016_g7_capability_compatibility`：Profile input slots、seed/determinism、输出媒体、GPU 并发，以及 extend/V2V/reference/motion matrix；生产 Published v12 `f63b3ac7-e264-4d44-adcc-eaf525aa1c34` 携带 PASS attestation `da5d5f66-daeb-4567-9bca-13335c7d95ed`。
- Migration `0017_g7_zero_public_network_e2e`：真实 loopback HTTP 服务驱动 Comfy/LLM/diagnostics client；socket guard 记录本地连接，`203.0.113.1` 在 connect 前被拒绝。生产 attestation `51b05922-fce9-496e-bb9e-a627ba346f77`。
- Migration `0018_g7_workspace_asset_authorization`：生产 KEYFRAME `0d389e44-0fc3-47e2-9edd-ee587ffdedf3` 重新计算内容 hash/size 后获得 `AUTHORIZED`；授权 `853fd791-0bc8-4cf5-bf12-fc54c3542caa`；BrandKit ACTIVE v1 `5a0d3a95-1aea-4482-a137-2a85439c8373`。
- Profile Editor 真 UAT：1440×900 只做一次 DRAFT→validate，1280×800/1024×768 只读；三档零溢出、零 console/page error、零失败响应、零原片请求；PASS 8/8，缩略图为 720px WebP。
- Adapter contract diagnostics UAT：1024×768 读取四类本地 adapter 声明，5 行（含表头）可见；零 console/page error、零失败响应、零水平溢出；视觉证据仅为 720px WebP `docs/evidence/g7/adapter-contract-visual-review-2026-08-14.json`。
- G7-05/G7-06 只读配置快照：`GET /api/v1/projects/{id}/configuration` 显示 ProductionPlanVersion、Profile binding matrix、DeliveryTargetVersion、冻结 Job/DeliveryPackage 影响；明确 Profile 切换不改写旧 Job、REMOTE transport 禁用；API 与三档 UI 验收通过。
- G7-03/G7-07 本地适配器契约：`GET /api/v1/adapters/contracts` 只读列出 Comfy loopback、OpenAI-compatible loopback、Local CLI、FFmpeg/FFprobe 四类 adapter；静态检查拒绝 REMOTE、公网 HTTP、URL 凭据和远程 executable，不打开 socket、不启动 runtime。

## 当前硬阻塞

- Migration `0019_g7_model_compatibility_reports` + `0020_g7_model_license_evidence` 支持离线完整 SHA-256、大小、safetensors header tensor/dtype、量化声明与项目内 license evidence 绑定；不启动 ComfyUI、不加载模型、不联网。
- H3 video VAE 报告 `d586967e-0634-4c8d-8887-193d398beda9`：SHA-256 `5a624684fad53d4acd0762aa7b07de4204de0bbb90f92c479605e326ccceb148`，5,207,806,104 bytes，560 tensors，F16 header 与 FP16 声明匹配。
- 报告状态仍为 `BLOCKED`：磁盘中没有可审计的真实本地许可证记录（`LICENSE_EVIDENCE_MISSING`）。manifest 说明和文件存在性不能冒充 license；只有操作者提供真实 record 后通过新 API 导入，才可推进 readiness。未伪造证据。

## 回归

- `pnpm api:test:safe`：108 passed / 4 Comfy live deselected（含 adapter contract tests）。
- `pnpm web:test`：9/9；`pnpm web:build`：TypeScript/Vite PASS。
- Ruff PASS；mypy PASS（83 source files）。
- 生产迁移 `0019→0020` 完成，在线备份与 `integrity_check=ok`，WAL；API 3210、Web 5173，ComfyUI 8188 无监听。

## 下一步

补入真实本地 license 证据后复跑报告；只有 G7 所有 blocker PASS 才能建立 G7 exit report 并进入 G8。最终目标仍需 86/86 FR、14/14 NFR、85 主干测试、完整本地 UAT、无 P0/P1、发布证据、安装/升级/回滚、SBOM 与 go/no-go。
