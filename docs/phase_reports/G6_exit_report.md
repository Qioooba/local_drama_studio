# G6 退出报告

状态：`PASS`（2026-08-14）。本报告只授权按顺序进入 G7，不代表 G7—G10、86/86 FR、14/14 NFR、85 个主干测试或发布验收完成。

## 退出演示结果

- 批准关键帧：MediaVersion `0d389e44-0fc3-47e2-b492-f7e3501ccf0c`，approval `c092a9f5-20fc-4165-bd4b-19ef8487d7cc`，SHA-256 `82f17d192cfe1759958fd10b1619575cec15e815d00873c9b4a1b79cf0f36203`。
- Published I2V proxy Profile：`7855a1b8-9be1-45bb-94b8-acdaf35bfde2`，WorkflowVersion `6e09d8c3-bb15-4c48-9253-cbdf609a8670`，352×640、21 sigma。
- 四个真实同源 proxy take：seeds 260826—260829；MediaVersion 分别为 `7a620549-6899-4ec4-a8ad-1974760ea761`、`8cc7c8d7-2b91-4605-923f-ac594b39f430`、`63e59d0d-1b85-4467-beae-10439eee63ee`、`2b5e49e4-09e0-4dc0-b208-29b33670589f`。四条均为独立 Variant/Job/Attempt/artifact，真实 SUCCEEDED，文件完整性与解码 machine QC PASS。
- 人工 proxy winner：Selection `e627ad4b-3751-4a94-9676-c146a28dcc12`，winner 为第四条 `2b5e49e4-09e0-4dc0-b208-29b33670589f`。人工视觉只使用 720×328、14—15KB WebP 接触表。
- Published I2V formal Profile：`2147a504-e7ae-4756-8e35-77de81c9cbdc`，WorkflowVersion `0414b7a2-db0f-4d8e-9d08-01f6f54ccd41`，512×928、31 sigma；其证据样片 MediaVersion `020f60ff-b66f-48a9-bb33-2991ba67e359` machine QC PASS。
- winner 的 `PROFILE_BRANCH` 正式 Variant：`87f9f260-50e3-4ed9-a514-7cae9ee0807d`，只切换至 formal Profile，保留 prompt/input/seed/approval 快照。Job `933c891f-5b11-4321-a9c9-2a9e68428115`、Attempt `8444b4ab-e3b2-41bd-83ef-c6ab394e0cbb` 真实 SUCCEEDED。
- 正式视频：MediaVersion `bf6f2151-a104-48d4-9c85-b89a7bad68c9`，artifact `98c8b8f0-afbe-4622-ba72-ef5b54019f10`，SHA-256 `68cbc0b06585adfe03611a56a3cf271690f11c36db05ba0c7b64e93d125a3ed8`，1,121,904 bytes，H.264 High、512×928、24fps、4.458008s、107 帧。
- 正式 machine check `4a04f522-9b2b-4d73-bae9-0df5ee059d32` PASS；人工 ReviewDecision `c1b3cf70-75db-4787-9be7-b9ab6585fa91` APPROVED。人工证据仅使用 720×328、18,092 bytes WebP 接触表。
- 只读 readiness `GET /projects/e5eaa01d-d39a-4a63-acbf-026da30b46e7/gates/g6` 返回 `PASS`，七项检查全部通过，`mutated=false`。

## 真实性与已知限制

- 四条 proxy 与正式片均由受控 loopback Production ComfyUI/H3 ephemeral Worker 生成，不使用 Mock；每任务结束后 Worker 停止并回收显存。
- 首次 formal 能力探针在完成 30/30 DiT 后因旧解码显存预留失败，失败 Job `93d99394-e825-4cdb-8202-39931e6d27ed` 与 Attempt `81a161e5-ffee-4360-b8de-057f58ca98f3` 保留。修复后的面积非线性预算在 proxy、formal、最大画布锚点验证，未抹除失败历史。
- 冻结提示含 candle/period costume，但批准源图实际为现代室内粉衣人物。正式审核只确认技术质量、动作与源图连续性，不声明错误语义目标已实现；该提示/源图不一致作为后续数据质量缺陷保留。
- 当前视频无音频流，因此本报告不声明音频交付能力；音频仍由 G8 验收。
- Figma Starter MCP 当日仍返回调用上限，本阶段未重复 foundations、未写画布；这不构成 G6 退出证据。

## 回归证据

- `pnpm api:test:safe`：79 passed、4 Comfy live deselected。
- `pnpm web:test`：4/4 passed。
- `pnpm web:build`：TypeScript 与 Vite production build PASS。
- Ruff：75 个 API source files 全部 PASS。
- mypy：75 个 source files、零问题。

结论：蓝图 09 的 G6 退出演示已真实闭环，允许开始 G7。G7 当前不是 PASS，G8/G9 仍只是 progress。
