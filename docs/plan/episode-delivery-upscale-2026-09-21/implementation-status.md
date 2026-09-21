# 整剧分集成片批量 AI 超分：实施状态

日期：2026-09-21  
结论：`CODE_IMPLEMENTED_REAL_NCNN_UAT_PENDING`

## 已实现

- 项目级 `/projects/:projectId/delivery` 工作区，含“分集成片 / 超分队列 / 版本与交付”三视图；单集交付页和侧栏均可进入。
- 最终成片来源投影、显式多选/全选、人工批准门禁、交付包人工批准门禁；来源策略 `PREFER_FINAL_DELIVERY` / `APPROVED_COMPOSE` 进入不可变选择哈希。
- 1080p 横竖屏几何、原生 2/3/4 倍推理后定量缩放或补边、跨方向确认、CFR/逐行/SDR/SAR/rotation/多视频流/磁盘预检、稳定黑边提示。
- 内置版本化预设、项目默认覆盖、本批高级参数、项目预设另存；执行 Profile 必须已发布、Runtime 必须 ACTIVE、adapter 必须匹配。
- 5 秒 `PREVIEW` 真实后台运行，独立幂等语义和受控内容接口；页面提供原片静音同步与样片单侧声音对比，样片不会登记正式 render。
- 原子批次提交、持久 Job/attempt/execution snapshot、共享 full-run 去重、分块 checkpoint、进度、暂停、恢复、取消、失败重试和 revision 冲突。
- `ncnn.realesrgan.video.v1` 生产执行器：逐块 FFmpeg 抽帧、Real-ESRGAN NCNN/Vulkan argv、逐帧数量校验、H.264 封装、源音轨复制或 AAC、原文件前后 hash 校验、原子发布。
- 模型选项冻结 `tile_fallback_sizes`：只在首块出现明确 Vulkan/内存 OOM 特征时按有限递减序列重试；普通程序/模型错误不回退。首块确定的实际 tile 会用于后续块并写入 checkpoint 与最终 receipt，恢复时从已验证块继承，前端高级参数区明确展示回退序列。
- 预检的磁盘安全阈值随 item 冻结；执行器在每块开始和最终发布前对 work/project 所在的每个不同卷重新检查。磁盘不足或 tile 回退耗尽进入 `NEEDS_ATTENTION` 并释放 lease，必须在释放空间/调整配置后显式重试，不会后台盲目循环。
- 生产执行器以可轮询 `Popen` 运行 FFmpeg/NCNN；Windows 使用执行器自有 PID 边界的 `taskkill /T`，POSIX 使用独立 process group，取消、超时或异常会清理整个包装器进程树且不登记正式结果。进程失败与 FFprobe 诊断会移除 argv 中所有绝对本机路径。真实父子进程树取消和路径脱敏已有隔离自动化覆盖。
- 暂停运行项后立即恢复不会让旧 attempt 继续运行或与新 attempt 并发：恢复意图先持久化，只有旧 attempt 取消/lease 对账并释放资源后才重新进入队列；从暂停态取消同样保持协作式停止。chunk、样片和正式 render 在各自写事务内再次核对 attempt ID、lease token、worker、有效期与 Job 状态，旧 attempt 无权借用新 attempt 的身份晚写。
- 最终 MP4 原子发布后会先写稳定 run/snapshot/hash 发布回执：若在 DB 登记前崩溃，新 attempt 会复核受控路径、源 hash、输出 hash/大小、完整 probe、帧数和 QC，再唯一登记成片/样片，不重跑模型或重写正式文件；若派生成片/样片已登记而 Worker 在 Job 完成前崩溃，则直接生成 `recovered_existing_output` 回执并完成原 Job。两个崩溃窗口均有故障注入集成测试。
- 超分队列提供“预览过期中间文件 → 显式确认清理”的两阶段操作，默认保留 7 天。计划和提交冻结同一 `eligible_before` 与计划哈希；提交前重新核对终态、活动 attempt、资源 lease、目录字节数和路径。只清理 DB 明确归属的 `work/jobs/<job>/ncnn-upscale`，symlink/junction/reparse 路径拒绝处理，源片、正式成片、交付包和 Job 回执均不在删除范围。
- 输出机器 QC 在发布前核对宽高、完整帧数、有理帧率、时长、yuv420p、逐行扫描、音轨数量和文件完整性；QC PASS 后仅创建 `SUPER_RESOLUTION` 候选，不替换 COMPOSE。
- SDR 色彩处理在计划中冻结：保留源矩阵/范围/primaries/transfer 证据，缺失字段按 SD/HD 规则推断并产生需确认警告；PNG 推理往返后执行真实 RGB→BT.709 limited 转换，而非只改 tag。输出 QC 另核对 BT.709 四元组、SAR 1:1 和 rotation 0。
- 人工审核、显式采用、selection optimistic revision、来源过期门禁；超分批量审核新增专用两阶段 plan/commit，每集独立填写 `episode_upscale` 检查项，冻结 render revision/文件 hash/合成根/机器 QC/模板版本/既有审核与逐集结论，提交时在单一事务内全部复核并写入。任一集漏项、stale、来源更新、QC 失败或文件变化均整批零写入，页面不提供“全部通过”快捷操作。批量采用与批量正式交付同样为服务端 plan/submit，不在浏览器循环制造伪原子操作。
- 正式交付文件按真实 probe 校验目标像素后再发布；manifest 记录 root compose、run、执行快照、来源描述、输出几何和冻结音频/字幕身份。队列 Job ID 同时作为稳定发布身份：若目录已发布但 DB 未提交，仅带精确操作标记的目录可恢复重建；若 DB 已提交但 Job 未完成则直接复用原包，官方文件不重写。批次只重试失败项，成功项和旧交付包保持不变。
- 已批准交付文件作为超分源时，会冻结其已烧录字幕与水印 Profile 快照；派生成片继续携带 `applied_effects`。正式打包请求相同效果时标记 `REUSE` 并跳过重复编码，要求去除或更换已烧录效果时以稳定冲突码阻塞并提示改用干净 COMPOSE 源。
- 音频封装保留全部映射音轨及 language/default/声道事实；兼容编码走 stream copy，PCM 等不兼容编码按冻结参数转 AAC，并在成片快照/回执记录 `audio_processing`。无音轨可正常完成；最终 mux 不再使用可能裁掉尾帧/尾音的 `-shortest`。
- 批量采用在写入前逐集验证批准、QC、来源和目标几何，任一项失败则零写入。横竖混合项目按每集实际输出像素匹配 1920×1080 或 1080×1920 的不可变目标版本；仅超分批次可复用已经显式采用且几何精确匹配的历史目标版本，普通交付仍只接受当前 ACTIVE 目标。
- 样片对比新增键盘可操作的 100% 原始像素同步裁切、横纵位置控制、原文件/非交付样片标识；分页全选复选框提供真实 `mixed` 半选语义。
- 本机真实 smoke 工具 `scripts/video_upscale/smoke.py`：不联网、不下载，实际跑两帧 NCNN 推理并记录程序/模型 hash、Vulkan index 和输出几何。它不会自行伪造发布状态。
- “系统 / 能力与模型”新增 NCNN/Vulkan 一键配置卡片：显式填写本机程序、模型目录、Vulkan GPU 与 tile，确认后按模型能力逐一执行 2×/3×/4×真实两帧推理；全部通过才登记不可变 RuntimeVersion、模型组件 hash、安装完整性、`UPSCALE_VIDEO` Offering、Profile smoke 并发布 Profile。失败不登记，重复提交幂等复用，不联网且不回显绝对路径。
- 一键发布的 Profile 锁定具体 `model_name` 与真实验证的 `native_scales`；计划阶段会用该倍率集合收窄几何计算，禁止以一个模型的 Profile 执行另一个模型或未验证倍率。运行前还会逐个复核冻结的 `.param/.bin` 大小与 SHA-256；原路径权重被替换时立即阻断并要求重新验证发布。
- 两阶段隔离验收脚本 `scripts/video_upscale/windows_uat.py`：`prepare` 在带所有权标记的全新绝对目录中建库，真实发布 Profile 并跑一横一竖两集、样片、幂等与暂停门禁；`finalize` 必须由真人逐个查看输出并显式声明后，才批准、采用、打包并落 probe/hash 证据。脚本不连接或清理生产实例。
- 新增应用层 `DatabaseUnitOfWork` 端口和可替换 service factory seam；超分应用服务不直接依赖 SQLite，也不在内部硬构造其他 Service。全部超分 JSON 路由及 NCNN 一键发布路由已有显式 FastAPI 响应模型，并新增架构债务守卫测试。

## 主要代码位置

- 数据与迁移：`apps/api/alembic/versions/0095_video_upscale_delivery.py`、`0097_video_upscale_previews.py`
- API：`apps/api/local_drama/api/routes/video_upscale.py`、`schemas/video_upscale.py`
- 业务：`apps/api/local_drama/application/video_upscale/`
- 执行器：`apps/api/local_drama/model_platform/application/ncnn_video_upscale_execution.py`
- 引擎配置与发布：`apps/api/local_drama/model_platform/application/ncnn_video_upscale_profiles.py`、`features/model-platform-v2/ModelPlatformCenter.tsx`
- 交付集成：`apps/api/local_drama/application/timeline.py`
- 前端：`apps/web/src/pages/ProjectDeliveryPage.tsx`、`project-delivery.css`、`features/production-settings-v2/VideoUpscaleDefaultsPanel.tsx`
- 运维：`scripts/video_upscale/smoke.py`、`scripts/video_upscale/windows_uat.py`、`scripts/video_upscale/README.md`
- 合同：`scripts/generate_client.py`、`docs/openapi/openapi.json`、`apps/web/src/generated/api.ts`

## 已执行自动化

以下均为隔离测试，不是实际 GPU/模型验收：

```text
pytest test_video_upscale_api.py test_video_upscale_execution.py
       test_video_upscale_geometry.py test_video_upscale_qc.py
       test_video_upscale_delivery_effects.py
       test_ncnn_video_upscale_profiles.py test_ncnn_video_upscale_process.py
       test_video_upscale_windows_uat.py test_video_upscale_architecture.py
       test_job_scheduler_progress.py test_g5_jobs.py
       test_migration.py test_release_migration_contract.py

Additional FFmpeg delivery regression: test_g8_timeline_delivery.py
  ::test_brand_watermark_and_compliance_versions_bind_delivery_without_auto_review

Web: ProjectDeliveryPage.test.tsx + VideoUpscaleDefaultsPanel.test.tsx
     + routeRegistry.test.ts
TypeScript: npx tsc --noEmit
Ruff: 超分 API / service / executor / smoke 脚本
Browser: Playwright CLI 受控 API mock，完成多选→预检→样片→入队→刷新恢复；
         1440×900、1280×800、1024×768 截图与 0 console error/warning
```

当前上列后端超分/队列/迁移定向矩阵共收集并通过 85 项测试；另有 12 项前端定向测试通过。新增覆盖逐集检查漏项、计划后 revision 变化时审核零写入，以及修复后的两集原子批准。水印实际应用/同 Profile 复用/移除冲突另由既有 G8 真实 FFmpeg 交付测试通过。该数字包含共享 Jobs 回归，不代表仓库全量 G5 已通过。

`test_video_upscale_execution.py` 使用标明为 `FAKE_ADAPTER` 的可控帧复制程序，但真实调用 FFmpeg，覆盖：预检、样片、幂等冲突、两个线程同 key 竞争只创建一组事实、两个批次共享同一 full run、取消单一引用不停止共享 Job、第二集建 link 故障时 batch/item/run/Job/snapshot/link 整体回滚、执行失败、显式重试、旧 attempt fencing、final rename 后 DB 前恢复、已登记输出恢复、中文语言/default 音轨保留、分块、派生成片、QC、人工批准、采用、1080p 正式交付和 manifest；还覆盖正式交付失败后的批次重试、成功/失败混合批次只重试失败项、交付发布前后两个崩溃窗口、已成功包不重建，以及过期中间目录在活动 lease/路径边界下的安全清理。`test_video_upscale_api.py` 另覆盖多候选/已采用版本稳定、根更新失效、批量采用整体回滚、横竖目标几何门禁和历史匹配目标的超分批次许可，并以 200 集、50 行分页、10 次 warm read 验证聚合读取最多 6 条 SELECT 且本机 p95 不超过 500ms。`test_job_scheduler_progress.py` 覆盖暂停后立即恢复的 settle 门禁、撤销待恢复意图、暂停态取消和资源耗尽人工重试；`test_ncnn_video_upscale_process.py` 真实拉起包装器和后代睡眠进程，证明取消后整个 owned process tree 退出，并覆盖 OOM 专属 tile 回退、运行期磁盘复检、Unicode/空格/命令元字符 argv，以及真实 FFmpeg 双音轨/无音轨/转 AAC 封装。它们证明编排、恢复和媒体封装，不证明 AI 推理或 GPU 兼容。

前端生产构建已通过 TypeScript、Vite 和 bundle budget；整剧交付页采用 lazy route，当前页面独立 JS chunk 约 36.38 kB（gzip 约 11.56 kB），独立 CSS 约 9.91 kB（gzip 约 2.21 kB），全站最大 chunk 约 295.2 KiB，低于当前 500 KiB 门禁。

浏览器证据见 `output/playwright/video-upscale-browser-evidence.md`。该回执明确标为 `CONTROLLED_API_MOCK`，只证明页面布局、交互和状态恢复；首次 1440×900 检查发现并修复了分集表“版本”按钮裁切问题，不计入真实 GPU/E2E。

## 尚未通过的门禁

以下项目必须保持 `PENDING`：

1. 当前机器的 `PATH`、仓库和已配置 `F:\AI_Projects\h3\model_manifest.json` 中未发现 Real-ESRGAN NCNN/Vulkan 程序或 `realesr-animevideov3` `.param/.bin` 权重，因此尚无可执行真实 smoke、设备映射、真实 Profile smoke 或 GPU 输出；一键配置入口已实现，但不能在缺少资产时伪造 PASS。
2. 未用用户认可的真实漫剧短片完成人脸、细线、文字、运动、暗部和闪烁人工检查。
3. 两阶段 UAT-A 隔离脚本已具备但因缺少真实资产尚未执行；通用 owned process tree 的自动化取消测试已通过，但 UAT-B/C 针对真实 NCNN/Vulkan 的进程树、重启恢复、显存/磁盘/吞吐证据仍未执行。
4. 三视口受控 mock 浏览器验收已完成；真实隔离 API/Worker 浏览器链路、完整键盘和屏幕阅读器走查仍未完成。
5. 已启动仓库全量 `scripts/check.ps1`：蓝图、compileall、OpenAPI/client 生成通过，安全 API 套件在现有“生产会话”等并行改动新增的架构债务处失败后终止。超分自身的数据库依赖、跨 Service 构造和缺失响应模型已全部从新增债务清单清零并有定向测试；全局 mypy 仍有其他并行改动的错误，故 G5 仍不能判 PASS。

因此 G1 的定向合同/迁移基本具备，G2–G5 不能判定完成；总体不得标 `IMPLEMENTED_AND_VERIFIED`。

## 真实机器收口

1. 将程序与权重放到用户管理目录，不复制进仓库；创建证据目录。
2. 推荐进入“系统 / 能力与模型 / 视频超分引擎”，填写现有程序与模型目录，确认后执行“验证并发布超分 Profile”。服务端会对声明的全部倍率真实推理；任何倍率失败都不会发布。
3. 也可先按 `scripts/video_upscale/README.md` 独立诊断，但脚本 PASS 本身不会发布 Profile；正式使用仍须在模型中心执行一键配置，使 Runtime/Model/Offering/Profile 与证据进入同一受控生命周期。发布后整剧页面应显示可执行 Profile。
4. 先按 `scripts/video_upscale/README.md` 运行两阶段 `windows_uat.py`，逐个查看输出并由实际审核人执行 `finalize`；它会记录一横一竖两集的 source/output probe、音轨、hash、设备与模型文件 hash。再补一组用户认可的 5–10 秒真实漫剧片主观画质验收。
5. 执行暂停/取消/重启与失败项重试，确认没有孤立 NCNN/FFmpeg 子进程、旧 attempt 不能晚写、完整 chunk 可复用。
6. 人工批准后批量采用并正式打包，重新 probe 每个交付 MP4，与 target 和 manifest 三方核对。
7. 完成三视口与键盘走查、全量检查，将证据写入 `docs/evidence/video-upscale/<date>/`，再按 G1–G5 判定。
