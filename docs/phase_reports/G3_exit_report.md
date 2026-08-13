# G3 阶段退出报告

- 基线：G2 `0001_g2_core` + G3 `0002_g3_config_media_import`
- 阶段结论：PASS
- 证据：`docs/evidence/g3/g3_validation.txt`
- 下一阶段：G4 版本、审核、选择与闸门

## 交付内容

- 只读 `model_manifest.json` 注册：canonical model root、禁用模型 guard、H3 route status、Comfy loopback Runtime、worker policy、Profile candidate version；没有隐式激活。
- `local_runtimes`、`model_artifacts`、`execution_profile_versions` 真实数据库同步；显式选择 candidate 时记录 `SELECTED_CANDIDATE`，正式生成仍要求发布状态。
- ProductionPlan/DeliveryTarget 版本表与项目绑定；交付只接受项目内 `LOCAL_FILESYSTEM` 相对路径，拒绝远程 transport。
- Media register/probe/hash：文件系统采用原子导入，SQLite 保存不可变 `MediaVersion`、probe JSON、来源名和 SHA-256。
- poster thumbnail、filmstrip、waveform cache 表及本地 FFmpeg 生成器；媒体内容 API 实现 HEAD、Range 206、416 和路径越界保护。
- 项目/集/镜头生产 read model：一次查询返回 revision、关键词/prompt snapshot、媒体/variant/job 计数、blockers 和 next action；FTS5 最小搜索。
- TXT/Markdown/DOCX source version、ImportSession、preview、extracted-text sidecar；未配置本地 LLM 时明确拒绝，不写伪造拆解草稿。
- 诊断中心记录 manifest、模型、节点/Runtime 边界、FFmpeg/FFprobe、磁盘、GPU、网络策略和 loopback Comfy 状态。
- React 生产台接入真实项目、Profile、episode read model 和诊断 API；通过真实后端页面采集截图。

## 门禁

- API 全量测试：20 passed，0 failed；包括 G2 回归和 5 个 G3 专项测试。
- Ruff：通过；mypy strict：40 个 Python 源文件通过。
- Web：Vite build 通过，Vitest 1 passed。
- 真实 UI：项目列表、Production read model、H3 candidate、诊断结果均已在本地浏览器加载并截图。
- 真实样片：本地 H3 MP4 已保存到 evidence，并用 FFprobe 验证 H.264/AAC、480×832、24 fps、107 帧、4.458333 秒。
- G3 migration 从 G2 数据库升级通过，运行时 DB integrity 仍为 ok；G0 计数仍为 86 FR、14 NFR、85 TC。

## 明确未完成

- G4 才闭环 Selection/Review/Machine QC、审批与收件箱；当前 read model 只显示 blocker。
- G5 才闭环持久队列、Idempotency-Key、lease/heartbeat/reconcile 和恢复证据。
- G6/G7 才闭环真实 ComfyUI/本地 LLM generation、Profile 发布与回归样片；本阶段没有伪造生成结果。
- 音频字幕、时间线、交付、画布、实验矩阵、规模/UAT/发布仍按 G8—G10 顺序实施。
- G11 legacy 迁移未实施。

## 回滚与恢复

G3 migration 以前置 online backup 保护；恢复后运行 `integrity_check`、重建 FTS/cache/read model。缩略图、filmstrip、waveform 是可删除重建的 cache，不是媒体权威。G3 只新增隔离工程和 evidence，未改蓝图目录或旧项目。

## 门禁结论

`PASS — G3 local configuration, media index and browseable production desk are evidenced; G4 may start.`
