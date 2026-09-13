# 第二轮最终 UAT 与对标改造闭环（2026-09-14）

## 结论

本轮已完成 `K01—K30` 的实现复核、真实缺口修复，以及《LocalDramaStudio_对标分析与开发实施方案》里 F01—F08 / PR-01—PR-10 的有价值增量。代表性真实链路已覆盖 6 镜、双人、同一旧屋场景、关键帧审核、真实 H3 I2V、候选采用、机器 QC、TTS、冻结音视频时间线、仅重合成、幂等回放、旧计划拒绝、重启恢复和第二集隔离。

本记录不把模型审美质量、未试听音轨或被用户要求中止的全量测试写成通过。EP1 当前仍诚实显示 `BLOCKED=4 / NEEDS_REVIEW=2`；镜头 5、6 的关键帧因模型质量被拒绝，没有无上限重抽。EP2 保持 0 镜，不被 EP1 操作污染。

## 服务与数据隔离

- 未停止或重启共享 API `:3210`、ComfyUI `:8188`、Ollama `:11434`。
- UAT API 使用 `:3223` 和 `work/uat-second-round/{data,projects,work,cache,logs,backups}`；只重启了该隔离 API 以加载新代码。
- 重启后项目 `132a3746-cf24-472d-9061-b599253ba602`、EP1 `4f7401c8-88b3-419d-8f7e-fe1a54fb0522`、6 个镜头、已采用视频、审核和时间线仍可读取；浏览器事实条显示 6/6 方案、6 项异常、声音 4/6、合成 1/6。

## 真实媒体与执行证据

- H3 I2V：Job `241f5d53-6380-4d62-b779-02576dabaff9`，Variant `574f1812-1b82-462b-abfb-6e645521569c`，Comfy prompt `0a9ae850-9a0a-4296-b826-da2cddaebd38`，MediaVersion `3872f367-248d-4c36-bd59-8dec99fdd098`。MP4 SHA-256 `4a34ffd9568b25782374ad4445d53a32fb1cf1725d96166f0336706f16d4d449`，684,789 bytes，10.125 s，480×832，24 fps，H.264 + AAC。
- 机器视频 QC `5659ec96-8e48-414e-b177-8788692f00ea` 为 PASS；9 帧联系表位于 `work/uat-second-round/logs/shot1-i2v-contact-sheet-9.jpg`。人工检查确认双人身份、旧屋场景和递信动作连续可用。
- TTS MediaVersion `838d2a02-135f-4495-9171-d666add06719`，2.625 s；机器音频 QC `1e39a17e-f9ff-4894-86e1-79316c7a7476` 为 PASS，覆盖容器解码、PCM s16le、22,050 Hz、时长、声道、响度、峰值、削波和静音。未执行主观听感试听，不作听感声明。
- 冻结音视频 TimelineRevision `9ef1e60a-cd4e-4f9c-b537-4723b4935916`（v4）。实际 `RECOMPOSE_ONLY` Run `f08046fa-f58e-46ef-887c-6c11614ae26a` / Job `6b05150b-8802-4e25-b49a-2ef9036a20be`，Render `23296662-500f-42b8-b59d-e21ec2b162f1`，SHA-256 `28fafbc249840127617777f84df03c3093b02e5144599bfe12900fac4c645077`，939,418 bytes，10 s，480×854，24 fps，H.264 + AAC stereo 48 kHz。报告：`work/uat-second-round/work/jobs/6b05150b-8802-4e25-b49a-2ef9036a20be/report.json`。
- 同键回放 Run `8fea92ac-a612-4480-b7b6-42de755cd608` 两次返回同一回执，第二次 `idempotent_replay=true`，Job `a6497232-5d0e-4530-a3ad-c0684b26b08f` 只有一个 Attempt/Artifact。
- 旧计划提交以 HTTP 422 / `EPISODE_OPERATION_PLAN_STALE` 拒绝且未创建 Run。新合同重启后只读预览得到 episode revision 6、timeline v4、GPU job 0、`mutated=false`、`runtime_contacted=false`、`network_contacted=false`；缺少预览 revision 的执行请求以 `EPISODE_OPERATION_REVISION_REQUIRED` 拒绝，之后 active job/run 仍为 0/null。

## K01—K30 / V01—V54 证据索引

逐 K 实现和聚焦回归分别保存在 `docs/evidence/phase1-k01...k08`、`phase2-k09...k13`、`phase3-k14...k21`、`phase4-k22...k26`、`phase5-k27...k29`、`phase6-k30...`。原方案第 7.2 节仍是 V01—V54 的逐项权威断言；本轮结论按下列证据层解释，不以文件存在代替验证：

| 场景范围 | 处置与证据 |
|---|---|
| V01—V14 / K01—K08 | 真实装配合同、人工字段保护、当前声音需求、多 LoRA、说话者身份、候选预算、重入幂等和多集状态归约均有对应 Phase 1 聚焦测试；本轮未发现反向回退。 |
| V15—V25 / K09—K13 | 不可变来源绑定、有界分层解析、请求身份/恢复、质量门与 apply 影响由 Phase 2 记录和现有 API 测试覆盖。 |
| V26—V42 / K14—K21 | 精确依赖、共享生成输入、帧桥、候选采用、当前媒体、分页批次、联系表和声音时长由 Phase 3 记录覆盖；本轮真实 I2V、QC、TTS、采用与 AV 合成补足 L3 代表样例，但不声明镜头 5/6 的模型审美通过。 |
| V43—V50 / K22—K26 | 稳定命令身份、Comfy 接受/恢复、真实依赖指纹、稳定重规划匹配和声明式拆镜继承由 Phase 4 记录覆盖；本轮同键回放、旧计划拒绝、重启持久化提供额外实证。 |
| V51—V53 / K27—K29 | 后端授权续接、零镜头准备和四种显式操作由 Phase 5 记录覆盖；本轮真实 `RECOMPOSE_ONLY` 证明不提交视频 GPU，新合同把 preview hash/revision/fingerprint 绑定到执行。 |
| V54 / K30 | 两集作用域隔离和有限推进已证实；EP1 保留真实阻塞，EP2 不受污染。没有把 EP2 0 镜写成“完整推进/交付通过”。因此 V54 的隔离断言通过，完整双集交付不在本轮声称范围内。 |

## 对标方案 F01—F08 / PR-01—PR-10

| 项目 | 最终处置 |
|---|---|
| F01 | 四种操作具有不同后端语义；预览/执行共享标准化输入、revision、plan hash、Profile/输入/合成指纹；旧计划零任务拒绝。 |
| F02 | `episodeProductionKeys` 与统一失效入口覆盖 overview/all/attention；页面、手动刷新、项目事件和顶部事实条共用权威重读。 |
| F03 | 异常列表为单页上限 100 的有界无限查询，按 shot ID 去重，显示总数/已加载数；整集确认使用服务端冻结范围。 |
| F04 | 主图、身份包、本集绑定新鲜度、生产预检分层；未检查显式为 `NOT_CHECKED`，无第二套可写 ready 真相。 |
| F05 | 草稿 owner/entity/token/version 注册与聚合；晚到 cleanup、保存期间新编辑、缺 save 回调均不误放行。 |
| F06 | 资产人物/场景/道具按组保留 `ACCEPTED/REJECTED/UNKNOWN` 回执；UNKNOWN 先查既有批次，再用原幂等键恢复；最终统一刷新事实。 |
| F07 | 顶部四阶段来自生产/后期/交付事实；当前集任务抽屉按 project + episode 服务端过滤，支持详情与原任务重试，键盘焦点可达。 |
| F08 / PR-09 | 选择器、查询、抽屉和引用输入已拆分；`@asset` 保存稳定 asset/binding/state ID，重名可区分、IME 安全、粘贴不伪造权限、过期绑定阻塞生成。 |

并行交付边界见 `docs/handoff/2026-09-14-f04-f08-delivery.md`。其 `GET /jobs` codegen 集成事项已在本分支完成：权威 OpenAPI、生成客户端和类型化 `listEpisodeJobs` 已同步。

## 回归状态（诚实记录）

- 聚焦 API 与 Web 回归均已在开发过程中通过；其中新增 jobs/asset API 聚焦套件通过，生产工作区/事件/引用/草稿/任务抽屉等 Web 聚焦套件通过。Ruff 与 `git diff --check` 在代码冻结前通过。
- 最终 Web 全量 JUnit 首次运行：594 tests，1 failure，文件 `round2-final-web-initial-failed.xml`；失败是音量输入 state 尚未提交即点击保存。增加等待后该文件 2/2 通过。
- Web corrective-1 全量 JUnit：594 tests，1 failure，文件 `round2-final-web-corrective1-failed.xml`；失败是审核 checkbox state 尚未提交即断言按钮。增加等待后该文件 8/8 通过。
- 用户随后要求“不要过度测试”。因此没有第三次 Web 全量；以生产构建（TypeScript + Vite + bundle budget）PASS 和两项直接聚焦 PASS 收尾。
- API 最终全量运行到约 60% 时有 2 个失败标记；按用户要求中止。pytest 在中止前未生成 JUnit/traceback，故失败名称和原因未知，不能写成通过，也不启动另一轮宽泛回归。代码冻结前相关新增 API 聚焦测试与 Ruff 已通过。

## 真实剩余项

没有已知、可复现且属于本轮 F01—F08/K01—K30 的产品代码缺口。仍未完成的只有：被用户中止的全量 API 最终结果、第三次 Web 全量确认，以及模型质量导致被拒绝的镜头 5/6。后两镜如要改善属于模型/素材创作选择，不应以无限重抽冒充工程修复。
