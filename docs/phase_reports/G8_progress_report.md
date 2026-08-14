# G8 进度报告（实现与自动化验证通过，阶段退出待补证）

状态：`IN_PROGRESS / AUTOMATED_PASS`。本报告不是 G8 exit report。

已完成：

- `0006_g8_timeline_audio_delivery` 真实 SQLite migration，包含字幕、音频绑定、增强 recipe/run、delivery events 和渲染版本元数据。
- 不可变 timeline revision 与 track item，包含媒体归属和时间范围校验。
- SRT/VTT/ASS 字幕生成、字幕重叠拦截、CPS 质量门禁和持久 revision。
- 本地音频绑定及授权状态约束。
- FFmpeg FrameAnchor 提取、连续性约束持久化、PENDING_REVIEW 状态。
- capability-driven 增强 recipe；不支持的 step 显式拒绝，支持的 step 使用本地 FFmpeg 并注册派生 MediaVersion。
- FFmpeg 整集渲染、LOCAL_FILESYSTEM 交付、manifest/hash 校验、篡改检测和撤回事件。
- OpenAPI、TypeScript client、Ruff、mypy、33 个 API 测试和真实媒体闭环测试。
- 2026-08-15 续跑：生成客户端补齐 G8 时间线 revision、字幕 revision、音频绑定、渲染、delivery build/verify/withdraw 操作，并加入 3 个 URL/body 契约测试；这些操作仍需真实输入和显式用户动作，不会自动写入生产集。

自动化证据：

- `docs/evidence/g8/g8_validation.txt`
- `apps/api/tests/test_g8_timeline_delivery.py`
- G8 read-only timeline/delivery status projection：`GET /api/v1/episodes/{episode_id}/timeline-status` 汇总真实 SQLite 中的 timeline、字幕、音频、render、delivery；无记录时明确显示空状态，`read_only=true` 且 runtime/network/mutation 均为 false。生产 `EPISODE_001` 真实观测为 0/0/0/0/0。
- 1440×900、1280×800、1024×768 浏览器验收：真实页面显示“暂无真实 revision/render/delivery”，三档均零 console/page error、零失败响应、零水平溢出；视觉证据仅为 720px WebP `docs/evidence/g8/timeline-status-visual-review-2026-08-14.json`。

未完成的阶段退出项：

- 浏览器截图、样片/交付包外部复核、完整 G8 UAT checklist 和阶段退出报告签核。
- G6 仍未退出，因此不能宣称整体 G0→G10 完成；当前 ComfyUI 任务不被本报告启动或干预。
