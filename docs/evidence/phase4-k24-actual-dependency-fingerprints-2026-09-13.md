# Phase 4 / K24：按实际消费对象收窄依赖指纹

日期：2026-09-13

## 结论

K24 已完成。整集预检不再把项目级所有参考、所有本地模型、未采用身份包版本、GPU/磁盘事实或非当前生产模式策略混进创意身份；生成前置身份与合成前置身份分别输出，并以版本化 schema 聚合为兼容的整集 `input_fingerprint`。

## 改造事实

- front-half snapshot 的 breakdown、proposal、镜头和身份包均限定当前 episode；别集新增草案不改变本集 snapshot。
- 身份包只纳入镜头显式绑定版本和资产当前采用版本；同一包中新建但未采用的 draft 不进入指纹。镜头切换到新批准版本会改变指纹。
- 资产参考只查询当前集已绑定资产，且指纹只纳入当前生效状态真正需要、ACTIVE、VERIFIED 的 Recipe 参考种类。
- 逐镜 Profile 身份在候选已生成前后保持稳定：指纹使用所有当前镜头的确定性 Profile resolution、Profile version 和 Workflow content hash；“还缺几条候选”、validation 时间、探测状态不参与创意身份。
- 本机模型路径/状态、Comfy 探测、GPU 容量、磁盘余量仍作为启动 gate，但不参与创意指纹。
- `generation_input_fingerprint` 使用 `episode-generation-inputs.v2`；`compose_input_fingerprint` 使用 `episode-compose-prerequisites.v1`。实际 Timeline Compose 仍使用自身按已选择视频/音频/字幕构建的 `compose_fingerprint`，未退化为项目级指纹。
- 帧桥仍只沿声明的硬后继生效；切镜边界不会按列表位置连锁失效。

## 验证

- 新增：别集 DRAFT_READY 草案不改变当前集 snapshot。
- 新增：未采用身份包 draft 不改变 snapshot；显式切换镜头身份包后 snapshot 改变。
- 新增：GPU 容量与磁盘余量变化不改变生成、合成或聚合创意指纹。
- `pytest test_episode_production_runs.py test_character_identity_packs.py test_frame_chaining.py test_frame_bridge_source_frame.py test_timeline_stale_refresh.py -q`：全部通过。
- `ruff check`：修改文件全部通过。

## 安全边界

未增加通用依赖图表，继续复用既有 stale、frame bridge、Timeline Compose 机制。测试只使用临时数据库与 mock 环境事实，未运行 GPU 或真实生成。
