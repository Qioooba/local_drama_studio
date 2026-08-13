# G4 阶段退出报告

- 基线：G3 `0002_g3_config_media_import`
- migration：`0003_g4_review_selection`
- 阶段结论：PASS
- 证据：`docs/evidence/g4/g4_validation.txt`
- 下一阶段：G5 持久任务队列、幂等、租约、心跳与恢复

## 已交付

- MediaVersion 显式 stage 与 parent lineage；本地媒体导入、派生版本、probe/hash/integrity 与真实文件路径保持不可变。
- Selection 与 approval 分离，支持 KEYFRAME、PROXY_WINNER、FORMAL_SELECTION，并按 stage 校验。
- 三类真实审核模板：image asset、proxy video、formal video；必填检查项、失败阻断、审核上下文与收件箱 API。
- Machine QC 持久化 file integrity/decode 结果；FORMAL 审核批准前必须有最新 PASS。
- ReviewDecision 记录 subject revision；镜头 revision 改变后相关审核自动标记 stale 并写审计事件。
- 批量审核 preflight/commit 短期 token 与 stale 检测，避免预检后对象被悄悄批准。
- React 审核收件箱从真实 API 读取模板/媒体/审核上下文，并可执行真实 media selection；没有静态业务候选。
- OpenAPI snapshot 与 TypeScript client 已按 G4 路由重新生成。

## 门禁

- 全量 API/domain/migration/media/review 测试：22 passed，0 failed。
- Ruff、mypy 43 个 Python 源文件、Web build、Vitest 全部通过。
- 真实本地 H3 MP4 已注册、ffprobe PASS、Machine QC PASS，且通过浏览器执行并验证了 PROXY_WINNER 选择动作。
- G4 截图、媒体样片、机器 QC、SQLite preflight backup/integrity 证据已保存。
- G0 基线复核仍为 86 FR、14 NFR、85 TC；本阶段只推进 G4 映射，不声称全量发布完成。

## 明确未完成

- G5：持久 Job/Attempt、Idempotency-Key、lease/heartbeat/reconcile、SSE 与 worker kill/recovery。
- G6/G7：真实本地 LLM 拆镜、ComfyUI semantic generation、Profile publish/capability truthfulness、变体/实验/首尾帧生产闭环。
- G8：连续性增强、视频审核、音频字幕、增强链、时间线、交付与 manifest/hash verify。
- G9/G10：ComfyUI Lab/业务画布、诊断扩展、规模/性能、安全/恢复/UAT、发布安装升级回滚和 SBOM。
- 云 Provider、API Key、计费、多租户和 G11 legacy migration 仍未实现。

## 回滚与恢复

G4 迁移前由 `scripts/migrate.py` 创建 online backup，并以 SQLite `PRAGMA integrity_check` 验证。G4 版本和审核记录均在 SQLite 中；源媒体不可变，缩略图等 cache 可重建。恢复后必须重新运行 migration head、integrity、FTS/read model 和阶段测试。

## 门禁结论

`PASS — G4 selection, review, machine-QC and stale-safe inbox are evidenced; G5 may start.`
