# Phase 3 / K14 — 逐镜 Profile 与精确生产依赖

日期：2026-09-12

## 结果

K14 已完成。整集与所选镜头生产入口现在复用同一条 `shot → episode → project`
偏好解析链，只对候选数量不足、确实还要生成的视频镜头检查 Profile、Workflow、
验证 attestation、节点/输入 schema 与已验证的运行时组件。已有足够的已验证视频不会被
无关的视频依赖阻断。

模型检查不再以 `bool(usable_models)` 作为通过条件。预检按精确 id、code、完整路径或
basename 匹配当前 Profile 声明的组件，并消费 Workflow 发布时保存的节点验证与 H3
runtime-layout 证据；不会在每次预检重新散列大权重，也不会按名称相似度替换组件。

逐镜解析出的 Profile 版本写入整集/所选镜头 Automation Workflow 的冻结 payload。
Worker 在创建 GenerationIntent、GenerationVariant 或 Job 之前重新解析并比较版本；漂移
以 `VIDEO_PROFILE_SNAPSHOT_STALE` 失败关闭。

TTS 预检已收敛到 `canonical_tts_requirements`。它只检查当前文本 revision 中尚无可复用
VERIFIED AUDIO 的实际发声行；唯一镜头角色优先，否则只做精确 speaker code/name 匹配。
旁白保持独立策略，不借用画面中唯一角色的声音。批量 TTS 执行也消费同一投影，不再有
第二套说话者/音色解析逻辑。

Comfy 健康探测仍受既有授权与端点策略约束；未配置/被策略拒绝时 I/O 标记为 false，
真实发起的成功或失败探测均标记 `runtime_contacted=true`、`network_contacted=true`。
探测结果不进入创意内容 hash。所有预检保持 `mutated=false`，且不创建 Job/MediaVersion。

## 关键验收覆盖

- 项目默认可用、shot 显式覆盖随后失效：逐镜阻断，不退回项目默认。
- 仅存在无关模型，同时当前 Profile 缺 VAE 与 Workflow 节点：分别报告精确缺项。
- 冻结 Profile 与执行时解析结果不同：在 Job 创建前阻断。
- 三个角色只有一人实际说话：只要求该说话者；新增未绑定说话者后才阻断。
- 旁白：报告独立 `NARRATOR_VOICE_REQUIRED`，不偷用角色音色。
- 当前文本已有已验证 AUDIO selection：直接复用，不要求再次生成或绑定外部 TTS。
- loopback 成功、失败、未配置：I/O 标记与真实探测行为一致。
- 预检前后 Job 与 MediaVersion 数量不变。

## 验证

- K14 音频、对白、生产运行、Worker、所选镜头批处理回归：94 tests passed。
- 一键整集 AUTO_CONTINUE 验收：1 passed。测试素材时长修正为与 4 秒镜头目标一致，避免
  正确的 `TIMELINE_SOURCE_DURATION_INSUFFICIENT` 防线把测试夹具误判为产品失败。
- Ruff：K14 涉及 Python 文件全部通过。
- `git diff --check`：通过；仅现有 Windows LF/CRLF 提示。
- 未连接正式数据库，未调用真实远程模型，未启动 GPU 生成，未下载或删除资产。
