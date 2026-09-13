# 最终全量回归与真实运行闭环（2026-09-13）

## 结论

第一轮自动化回归与下列有界实测通过，记录保留。第二轮复核更正：本记录不证明 Phase 0—6、K01—K30 的全部验收断言已完成。真实 4—6 镜用户闭环、实际局部重跑/仅重合成、两集一集阻塞而另一集实际推进等仍需补足相应证据。E01—E09 按启动条件裁决，并随真实功能问题复核。

## 最终质量门禁

- API：`python -m pytest apps/api/tests -q` 退出码 0。共收集 242 个测试文件、1,423 项测试；1,422 项通过，1 项按 Windows 平台预期跳过。跳过项为 `test_runtime_release_architecture.py:242` 的 POSIX 文件权限合同，不适用于当前 Windows 主机。
- Web：`npm test -- --run` 退出码 0；138 个测试文件、578 项测试全部通过。
- 生产构建：`npm run build` 退出码 0；TypeScript 检查、Vite 构建和 bundle budget 全部通过，最大包为 461.7 KiB。
- Python 静态检查：`python -m ruff check apps/api/local_drama apps/api/tests scripts` 通过。
- 架构债务阻断：`test_architecture_debt_manifest.py` 3 项全部通过；官方债务清单已刷新为当前真实基线。
- 补丁完整性：`git diff --check` 通过；仅输出 Windows LF/CRLF 转换提示，无空白错误。

## 真实运行与用户侧证据

- 本地 Ollama `qwen3.8:27b` 完成真实推理，证据：`phase6-real-ollama-smoke-2026-09-13.json`。
- RTX 3090 Ti / ComfyUI 0.33.1 通过平台 Job 生成 480×832、24 fps、H264 + AAC MP4；机器 QC PASS、人工 APPROVED、正式采用及媒体哈希闭环，证据：`phase6-h3-real-platform-uat-pass-2026-09-13.json`。
- 正式 FFmpeg 合成、交付 manifest、交付核验与 HTTP 200 下载通过，证据：`phase6-delivery-real-v2-2026-09-13.json`。
- 代表性视频仅抽查三帧联系表；雨夜旧屋主体与构图稳定。错误 UI 首帧样本被人工否决，未冒充合格故事素材。
- 隔离浏览器完成两集有限选择、集中阻塞、跨集切换、操作影响预览以及 API 真实重启恢复，证据：`phase6-user-perspective-browser-and-restart-uat-2026-09-13.md`。

## 保留边界

- 生产 API、生产数据库、真实 ComfyUI 和 Ollama 服务未被停止、迁移或写入测试数据。
- 浏览器验收使用生产数据备份快照与独立 UAT 根目录；没有把受控单镜小样解释为整部成片的逐帧画质保证。
- 当前仅有 Starlette `httpx` TestClient 与 Alembic `path_separator` 的上游弃用警告；不影响本轮功能和发布门禁，但可在后续依赖维护窗口处理。
- 验收结束后已停止隔离 API `127.0.0.1:3222` 与 Vite `127.0.0.1:5174`；生产 API `127.0.0.1:3210` 仍返回 `HEALTHY`，ComfyUI `127.0.0.1:8188` 与 Ollama `127.0.0.1:11434` 均保持在线。
