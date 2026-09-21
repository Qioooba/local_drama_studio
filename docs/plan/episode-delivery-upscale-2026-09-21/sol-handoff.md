# 交给 Sol 的完整实施指令

本文件最初用于后续实施；主链路现已落地。继续开发前先读同目录 `implementation-status.md`，不要重复已完成工作，也不要把尚未执行的真实 GPU/UAT 写成通过。文档基线：2026-09-21、`04829fe`及其后当前工作区。

## 可复制任务

> 请实现本项目的“整剧分集成片批量AI超分”完整功能。先完整阅读同目录README.md、development-design.md、test-and-acceptance.md，并核对当前代码和AGENTS.md；按照设计完成所有首发WP01–WP09及验收G1–G5。
>
> 用户场景是一部漫剧各集都是480p，在项目级整剧交付页多选各集最终成片，应用漫剧1080p预设，试跑、预检、加入持久后台队列，支持进度、暂停、取消、重试和恢复。结果保留来源、新建派生整集成片，人工审核后采用，再批量生成真正1080p的交付包。模型、执行程序/脚本、参数、预设和项目默认可配置。
>
> 本次要实现端到端闭环，不停在计划、前端按钮、占位模型、通用FFmpeg放大或仅CLI输出。首发默认采用经验证的Real-ESRGAN NCNN/Vulkan动漫路线；扩展适配器可以预留，但未实现/未验证的能力不要显示为可用。
>
> 复用现有Model Platform执行快照、Job/attempt/lease、GPU互斥、整集render/review/delivery。注意现有render并不是普通MediaVersion，现有“最新render才能交付”规则需要按设计升级为合成根和显式采用派生版本，修复所有受影响读模型。项目生成480p不因超分变成原生1080p。
>
> 当前工作区可能存在用户修改；先查看git状态并保留无关内容。迁移号以当前head分配。按依赖顺序先完成一集真实纵向切片，再完成批次、UI和交付。每项实现后做必要测试，最后完成合同生成、项目必需check和真实Windows/GPU验收。不要对生产全剧自动跑耗时验收；使用隔离fixture及明确选定的真实短片。
>
> 遇到安装包/权重缺失，先完成所有可独立完成的代码、schema、UI、隔离测试与配置向导，精确报告缺少的文件/版本和真实验收缺口。未做真实GPU测试不得声称已全验收。不要用下载失败作为提前停止全部可做工作的理由。
>
> 最终交付代码、迁移、脚本、操作说明和证据，报告完成状态、验证结果和剩余阻塞；不要自动commit、push或修改其他任务，除非用户另有明确指令。

## 1. 开始前应确认的源码

| 目的 | 当前入口 |
|---|---|
| 路由与导航 | `apps/web/src/app/routeRegistry.ts`、`router.tsx`、`layouts/AppShell.tsx` |
| 交付与设置 | `pages/DeliveryPage.tsx`、`ProductionSettingsPage.tsx`、`features/production/DeliveryWorkflowPanel.tsx` |
| 当前后处理 | `features/generation/PostProcessPanel.tsx`、`application/timeline.py`增强与合成方法 |
| 模型配置 | `pages/ModelsPage.tsx`、`features/model-config/`、`model_platform/application/` |
| 正式模型提交 | `execution_planning.py`、`execution_submission.py`、`execution_snapshots.py`、两套handler registry |
| GPU与进程 | `application/job_resources.py`、`gpu_runtime.py`、`infrastructure/gpu_lifecycle_adapters.py` |
| 当前成片/批准 | `application/episode_render_approval.py`、`timeline_status.py`、`reviews.py` |
| 交付 | `background_operations.py`、`timeline.py::build_delivery`、`worker_handlers/delivery_build.py` |
| 聚合投影 | `infrastructure/database/post_repository.py`、`product_context_repository.py`、`review_decision_repository.py`、`g8_readiness.py` |
| 类型/客户端 | `scripts/generate_client.py`、`docs/openapi/openapi.json`、`apps/web/src/generated/api.ts` |
| 验证 | `scripts/check.ps1`、`test_api_safe.ps1`、pytest/Playwright现有配置 |

路径在表内按web或api模块说明缩写；开发设计给出更完整的上下文。重新搜索所有 `episode_render_versions` 查询，不要只改以上列表中的一两个文件。

同日工作区已出现V6音频/字幕渲染合同和跨横竖屏显式确认修订，开发设计§2.2已纳入。超分保留最终成片音轨，不重新混入镜头模型原声；继承已有`subtitle_burned_in`证据。核对最新diff后实现，不将这些修订回退到文档最初勘察的旧状态。

## 2. 建议实施节奏

1. 固定source descriptor、output geometry、preset/参数合同、派生版本与选择语义；编写对应纯函数和DB测试。
2. 迁移和resolver；确保旧COMPOSE/交付测试仍通过。
3. NCNN runtime安装和Profile发布、GPU生命周期、执行context、可取消runner；真实处理一小段。
4. 分块/checkpoint/音频/mux/QC/派生登记；完成一集真实输入到新render。
5. 原子批次、后台检查、dedupe、重试和重启对账；完成两个并发提交的竞争测试。
6. 预设/项目默认/样片，搭建整剧页面和模型接入向导，接单集入口。
7. 审核、采用、target派生、批量打包、manifest和所有旧读模型兼容。
8. 跑三视口和UAT-A/B/C，补操作文档和证据，执行集成检查后交付。

这不是分期删减；用户期望一次完整实现。可拆内部工作包，不能将首发的恢复、审核采用或正式打包标为“以后再说”。

## 3. 容易误做的点

- 把reviewInbox中的镜头视频当作各集最终成片：错误，整集render是独立表。
- 把FFmpeg scale或Lanczos叫AI超分：错误，必须有真实模型执行证据。
- 只加UPSCALE_VIDEO枚举：现有代码已有枚举，缺的是具体执行与业务闭环。
- 用同一渲染表插入SR后继续`ORDER BY created_at DESC LIMIT 1`：会污染最新主成片/批准和选择。
- 照抄源production_spec验证SR输出：480p旧规格会拒绝1080p新结果。
- 发布模型就宣称可执行：需runtime、模型、参数合同、handler、真实smoke同时就绪。
- NCNN当PYTORCH或COMFY调度：需独立runtime所有权，共享同物理GPU互斥。
- 把Vulkan index0等同CUDA index0：需要验证映射。
- 在浏览器for循环提交N个独立请求并称“批量原子”：必须服务端事务和幂等。
- 每次轮询GET跑完整hash/ffprobe：检查异步缓存，执行前再校验。
- 抽全片x4 PNG：空间随片长爆炸；使用有界frame chunks。
- 捕获单帧异常后继续写视频：会丢帧/音画不同步，必须失败该块。
- 暂停UI后立刻释放GPU而child仍跑：需要进程停止确认和attempt fencing。
- 相同run被两个批引用时随便cancel：必须按活跃引用处理控制影响。
- 所有模型都显示相同参数：只展示当前合同支持项，NCNN无效字段不能混入。
- 默认补到60fps、开人脸修复、自动换模型：不属于默认超分预设。
- 已烧水印的源再次水印：必须追踪applied_effects和target冲突。
- 包里写1080p但实际MP4还是480p：必须实测输出并与manifest核验。
- 自动把QC成功当人工批准：审核和采用必须保留明确事实。
- 修改`generated/api.ts`但不改generator：下次生成会丢功能。
- 真实GPU测试被safe suite自动跑：新marker和默认过滤需同步。

## 4. 最终交付清单

- [ ] WP01–WP09首发范围全部实现，未支持扩展不伪装可用。
- [ ] 可从项目与单集页面进入，跨页多选与参数覆盖可用。
- [ ] 实际引擎/模型可配置、验证、发布，至少一条真实动漫路线通过。
- [ ] 480p→1080p目标准确，原fps/音轨/字幕完整。
- [ ] 批次、Job、进度、共享run、取消/暂停/恢复和崩溃对账有测试。
- [ ] 结果登记为新派生成片，旧成片/交付包不被覆盖。
- [ ] 对比、人工审核、采用、source过期、原版回退闭环。
- [ ] 1080p交付target与批量打包实际验证，manifest来源完整。
- [ ] API、迁移、生成client、样片/runner/运维脚本和安装定位完整。
- [ ] 测试矩阵绑定test/证据，G1–G5结论明确，SKIP不当PASS。
- [ ] 现有用户改动保留，最终git diff范围可解释，无未经请求的提交或外部发布。

建议最终报告格式：完成行为；关键实现文件；测试统计；真实运行的程序/模型/设备；产物与证据路径；明确未覆盖范围。少叙述执行过程，多给能复核的结论。
