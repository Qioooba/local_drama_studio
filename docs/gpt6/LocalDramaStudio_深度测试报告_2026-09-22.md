# LocalDramaStudio 深度测试与修复报告

**审计对象：** [Qioooba/local_drama_studio](https://github.com/Qioooba/local_drama_studio/tree/8a63c604a1a13556dbe277d312ecf73bebb52883)  
**固定提交：** `8a63c604a1a13556dbe277d312ecf73bebb52883`  
**报告日期：** 2026-09-22（北京时间；测试日志主要采用2026-09-21 UTC及执行机当地时间）。  
**交付性质：** 本轮测试结论、完整问题定位、具体实现方案与回归验收任务。产品源码未修改；最终1884/1884份源文件Git blob SHA与固定提交一致。

## 1. 结论与判断

**当前提交不能直接判定为“从零安装后可以可靠地把长篇小说制作到完整交付”。** 严格锁定依赖可见的环境无法导入主应用；补依赖后的新库/API/Web可以运行，也确实跑通了真实CPU后期交付链，但仍存在原稿丢失、覆盖率失真、任务停止意图丢失、产物完整性和项目包往返问题。这些会影响内容是否完整、编辑是否保留、任务是否真的停止、成片是否与时间线一致，优先级高于视觉微调。

本报告登记 **58项问题与改进条目**：21项P1、36项P2、1项P3。其中包括动态复现的业务缺陷、源码确认的界面问题、工程门禁问题与NP09格式识别限制；FE-09的P1依赖缺失randomUUID这一环境条件。**不能将这些数字宣传为“全部都是实机动态复现的BUG”。** 同一根因引发的多条pytest失败没有重复计为独立缺陷。

P1表示应优先处理的启动阻断、内容/状态/产物正确性或主流程操作问题；P2表示重要能力、错误合同、兼容或可恢复性缺口；P3为局部工具健壮性问题。本轮没有依据宣称存在P0级灾难性事故。

完成的是：建立固定版本的全功能/接口库存，执行全部收集到的后端用例、全部前端既有测试、现有Go运行宿主测试，启动真实前后端，补充实际文稿、状态、文件、媒体和UI组件对抗测试，逐条写明修复与验收。**未将不能执行的真实浏览器/GPU/Windows验收勾成通过，也不作“绝无遗漏”的保证。** 631条接口清单逐条保留状态；接口存在、源码读过、调用过、断言通过和全输入覆盖是不同含义。

主要证据位置：`evidence/final_source_integrity.json`、`evidence/backend_tests/pytest_baseline_summary.json`、`evidence/ui_audit/summary.json`、`evidence/novel_pipeline/real_novel_import.json`、`evidence/media_pipeline/happy_path_complete/results.json`。下文和附录包含每个条目的原始证据及当前代码位置。

## 2. 版本、环境与“从零”方法

| 项目 | 本轮实际条件 |
|---|---|
| 源码 | 当前main固定到上述提交；1884个运行/构建/测试相关文件逐Git blob校验；4388项仓库树已入证据 |
| 未下载历史目录 | `.codex_backups`、`docs/research`、`docs/visual_audit`、`docs/evidence`内历史资料/截图；不以旧截图作为本轮证据 |
| 获取方式 | 网络clone未完成，改由GitHub原始文件逐文件获取与SHA验证；不是声称成功完成git clone |
| 后端 | Linux x86_64、Python3.12.14；独立新SQLite目录、真实Alembic迁移；未使用用户本机数据库 |
| 依赖条件 | 主测试工具环境额外有pypdf6.19.0；另建只含锁文件38个准确版本依赖的可见性环境，独立证明首次导入失败 |
| 前端 | Node24.19.0；仓库声明pnpm9.15.9，环境pnpm11.19.0；安装策略差异与jsdom/AbortSignal适配单列 |
| 媒体 | 真实FFmpeg/ffprobe6.1.1，CPU合成测试图、音频与视频；未安装用户生成模型 |
| 原生运行宿主 | Go1.25.0；Linux运行测试、Linux与Windows amd64交叉编译 |
| 硬件 | 当前无可用GPU/用户模型/Windows桌面；不报告真实显存、推理速度或模型质量 |
| 浏览器 | 官方浏览器访问安全检查无法取得审批决定，未获访问权限；真实页面操作/截图为0 |
| 数据隔离 | 独立项目、数据库、文件夹、模型能力夹具；用完的测试状态不写回生产源码或用户数据 |

“只锁依赖环境”通过只读复用已安装的精确版本包构造，不等于重新联网pip下载所有包或完成Windows离线安装包验收。后续具备额外依赖的功能测试成功，不能反过来证明锁文件正确。媒体成功链还显式补建了内置审核模板，详见BKT-08；没有把这一步隐藏为原生启动自动成功。

当前入口由[前端路由表](https://github.com/Qioooba/local_drama_studio/blob/8a63c604a1a13556dbe277d312ecf73bebb52883/apps/web/src/app/router.tsx)和[API应用启动](https://github.com/Qioooba/local_drama_studio/blob/8a63c604a1a13556dbe277d312ecf73bebb52883/apps/api/local_drama/main.py)确定。React工作区调用FastAPI；应用服务以SQLite/WAL保存版本、任务、引用和审核状态；worker依据租约执行Comfy/本地AI或FFmpeg，媒体产物再进入候选、采用、时间线、审核和交付。分集规划、资产、导演、声音、后期、审核和模型配置是可区分的功能域。当前没有独立的“输入一句话创作整部原创长篇小说”主入口；小说主流程是导入/粘贴已有原稿后生成改编与制作内容，QuickCreate是媒体试验工作区。

## 3. 测试执行结果：不同层级分别计数

| 层级 | 实际执行与结果 | 正确解释 |
|---|---|---|
| 源码完整性 | 1884/1884 SHA匹配 | 覆盖本次取得的运行/构建/测试源码，不含历史截图目录 |
| 后端收集 | 261测试模块、1587展开用例 | 常规1583＋Comfy标记4；不是1587通过 |
| 常规pytest原始基线 | **1351通过、230失败、1 setup错误、1跳过** | 四片1583唯一用例，无重复；条件复测不改写原始结果 |
| Comfy标记用例 | 4个均实际尝试，前提缺失失败 | 不具备本机清单/模型/运行服务，不能证明真实推理好坏 |
| 后端失败复测 | 原先非PASS中212个在明确TEST_ONLY条件下通过 | 不是一次全套通过；合并不同前提曾PASS共1563，仍20未曾PASS（含1原跳过） |
| 新库迁移 | 首次、重复迁移均通过；234表；integrity=ok；foreign_key_check=[] | head=`0100_production_session_waiting_user`；在线WAL另有专项 |
| 编译/风格/契约 | compileall、Ruff通过；OpenAPI/TS client语义一致 | CRLF/LF字节差异不算契约漂移 |
| 后端类型 | Linux105错误/29文件；Windows目标93错误/27文件 | 静态门禁不通过，不能把每条诊断算独立运行BUG |
| 前端生产构建 | 类型检查、Vite、bundle预算通过；502模块、54chunks、最大295.2KiB | 能构建不等于界面功能全部正确 |
| 前端原配置 | 152文件、648例：641通过/7失败，8未处理异常 | 6例与Node/jsdom AbortSignal realm有关，另1次不稳定 |
| 前端环境适配后 | **152文件、648/648通过，0未处理异常** | 保留原生AbortController/Signal，不改业务代码或业务断言 |
| 新增前端问题复现 | 5文件、14个坏行为断言成功 | 对应FE01–10子场景；不是修复通过，FE11–14为源码确认 |
| 主真实HTTP | 171请求、153不同操作，136次GET巡检 | 98个GET未在该轮独立调用，所有631操作仍入清单 |
| 主HTTP显式状态断言 | 原31符合/4不符；复核后34符合/1产品错误 | 3项为测试预期纠正，保留原始记录与裁定 |
| 真Vite→API→SQLite | **10/10通过** | 原生代理、真实建项目/上传/读段落/确认；不模拟后端 |
| Vite页面入口HTTP | 6/6返回预期内容 | SPA fallback不是浏览器渲染、点击或视觉验收 |
| 小说定向专项 | 15组：4正常、10缺陷复现、1能力限制 | 这是针对边界的补洞集，不是项目通过率 |
| 仓库长篇实际导入 | 86609字节、29855字符、738段、42章；导入/确认/预检成功 | 正式AI启动422，PIPELINE_LLM_REQUIRED；没有生成长篇短剧 |
| 状态/安全专项 | 26场景：13异常、12正常、1条件边界观察 | 归并10问题；含真实SIGKILL、SQLite与文件边界 |
| 真实CPU媒体闭环 | 12阶段完成，8秒/192帧/320×180/24fps H.264+AAC | 合成画面和Flite英文语音，显式TEST审核，非AI生成 |
| Runtime Host | 29顶层测试通过；含子case34个PASS事件 | 不将子case再重复加总 |
| Go构建 | 3模块×2目标=6/6通过 | Windows仅交叉编译，未运行Windows安装/UI |
| 真实浏览器视觉 | **0项，环境阻塞** | 没有本轮页面截图；仅完成源码/CSS及jsdom交互审核 |

以上是不同粒度、不同前提的证据，不计算一个混合“总通过率”。主HTTP的153个操作也不能当成全部API自动化覆盖率：现有pytest和专项还会在服务/API层调用其他操作，但没有为它们伪造统一的网络覆盖统计。完整API清单记录577路径、631操作（GET234、POST361、PUT18、DELETE9、PATCH6、HEAD3），每项都解析到当前源码函数。

### 原始后端失败如何裁定

230个失败加1个初始化错误，共231项已逐项入册：194项直接缺model_manifest、7项为其间接断言，共201项清单/测试前提阻挡；17项由BKT-08审核模板初始化耦合引发；12项与平台、机器配置或跨平台测试假设相关，其中2项揭示Linux僵尸进程条件下的BKT-10，不能全部解释成无关环境问题；最终裁定见附录A；另1项SQLite I/O异常单独复测通过，保留瞬态记录。这个分类按原始首个触发原因归并，补fixture后显露的新问题或新环境前提另记，不改原始状态。

## 4. 功能域覆盖与剩余边界

| 功能域 | 已完成工作 | 结论/仍需的真实验收 |
|---|---|---|
| 安装、配置、依赖、启动 | README锁检查、隔离导入、API/Web真实启动、健康检查 | BKT01/08、HTTP04；Windows从零安装仍待实机 |
| 数据库、迁移、备份 | 新库/重复迁移、完整性、外键、WAL备份、强杀事务 | 基础正向通过；不完整库探针失真 |
| 项目/分集/场景/镜头 | 新建、预检、追加、版本更新、重复/跨项目边界 | HTTP01/02/03；同类多实体写入列入回归 |
| 原稿上传/粘贴/预览/确认 | UTF8/GBK、空白/空文件、坏文件、真实42章长篇 | NP04–10、FE01/02；大批输入结构与版本须修 |
| DOCX/EPUB/PDF/Markdown | 真实解析器与最小格式夹具、坏PDF拒绝、文本来源校验 | DOCX/EPUB边界可复现；不宣称完整Office/Calibre兼容认证 |
| 全剧规划/改编/续接/应用 | 原稿范围、章节划分、长章/多章、草案应用与错误恢复 | NP01/02/03/11；LLM替身只验证结构逻辑 |
| 资产库/人物/场景/道具/身份包 | 当前入口、引用与参数、既有测试、真实图片导入/QC | 真文生图与身份一致性待模型；FE05/09/13 |
| 导演/镜头方案/生成偏好 | 当前导演页及后端版本/参数/候选合同、既有测试 | 部分旧组件不可达，不能当当前入口；真镜头效果待模型 |
| Production Factory | 会话/审核/预算/状态源码与既有测试、状态专项 | FE04/11/12；READY启动恢复需完善 |
| QuickCreate/Visual Lab | 当前媒体工作区源码、参数调用、缺能力场景 | FE09/14；没有将其当成长篇原创小说工具 |
| 模型/Profile/Workflow/Comfy | 预检、声明、编译/绑定测试、本地HTTP产物收集夹具 | BKT02/07、MED01；真实服务/权重/GPU未具备 |
| T2V/I2V/首尾帧/Ref2V/运动控制 | 能力合同、编译测试及入口检查 | 未完成真实H3推理、画面质量和显存验收 |
| 配音/音色/对白/口型 | 候选导入采用、真实音频QC、Vox参数转发验证 | MED04；中文TTS、真实克隆/LatentSync待验 |
| BGM/SFX/混音 | 真实文件绑定/循环/增益/时段、采样和整集合成 | MED02；end_us后实测仍有不该存在的声音 |
| 字幕/文字来源/烧录 | 脚本SRT冻结、真实烧录、特殊路径对照、真实文件校验 | MED06；Windows中文字体/逐cue视觉仍待验 |
| 时间线/保存/冻结/播放 | v3 revision、组件dirty/seek、真实合成/导出对照 | FE07/08、MED03/05；浏览器播放/拖动未实测 |
| 缩略图/filmstrip/waveform/proxy | 全部实际生成文件、帧提取与越界拒绝 | 正向通过；不以小色块衡量真实长剧吞吐 |
| 超分/后处理 | 真CPU recipe、320×180→640×360、QC、既有NCNN测试 | 不是Real-ESRGAN神经推理；Vulkan/显存待验 |
| 机器QC/人工审核/采用 | 导入媒体QC、显式测试审核、未批准交付422 | BKT08、FE05/10/11；测试审核不能充当发布批准 |
| 整集渲染/最终交付 | 真实持久任务→8秒成片→批准→verify→HTTP下载/Range | MED01–08相关风险；已产生可复核成片 |
| OTIO/EDL/剪映草稿 | 真实导出结构、媒体复制、时间与效果对照 | MED05/09；未在第三方编辑器打开 |
| Job/Attempt/Lease/取消/恢复 | 并发、状态矩阵、过期恢复、实际进程强杀、真实FFmpeg取消 | SS01/02/03/09；真模型长任务取消仍待验 |
| 自动化/webhook/outbox | 当前入口和既有测试、真实本地投递边界 | SS10；未向用户外部账户或生产URL发送消息 |
| 项目包/导入/复制/恢复 | 实际ZIP导出、重导、副本往返、坏包、路径边界 | SS04/05/08、HTTP03 |
| 文件hash/路径/令牌/Origin | 篡改、越界/符号链接/ZIP路径、token和Origin拒绝、脱敏 | SS06/07；LAN风险单列为条件观察 |
| 审计/诊断/容量/质量门禁 | HTTP读取、日志/契约、现有测试、mypy与维护脚本 | BKT03–06、HTTP04；无真实GPU性能结论 |
| UI导航/弹窗/键盘/布局 | 23个pages模块及4个直接feature视图、53份CSS、648既有测试与14专项 | 具体交互有证据；所有实际视口、IME、读屏和像素审核仍未验证 |

完整页面与真实URL入口、不可达旧模块边界在附录G；完整API逐行状态另交付《接口覆盖清单》。这种库存用于防止功能被遗忘，不能替代每个分支的动态测试。

## 5. 两条真实链路的结果与限制

### 5.1 仓库长篇《照骨灯》的真实导入

使用仓库自带《照骨灯_凡人修仙原创长篇_约200分钟.txt》，实际上传86609字节，SHA-256为`aadea09ebef821c0399c034c951c0261eff23ccc417a72c1dff4cce036c49d21`。解析得到29855个Unicode字符、738段、42章，归一化后的全文与源文一致；上传201、确认200、预检200。样本本轮单次上传解析约0.591秒、确认约0.064秒、预检约0.165秒，这些是隔离环境的观察值，**不是性能基准或用户机器预测**。

真正启动生成返回422 `PIPELINE_LLM_REQUIRED`，数据库中pipeline_runs=0、jobs=0。文件名“约200分钟”是样本标题，不是生成视频长度。该文件前5段主要为标题/规模/简介；不能把这些排除都称为丢失剧情。NP01另用含独有剧情的序幕样本证明正文覆盖缺口，NP02用61章和超长章证明批次/截断/续接问题。模拟LLM只用于使结构服务在无模型时可检验，未评价真实模型理解与创作水平。

### 5.2 可实际播放的8秒后期交付样本

使用两段4秒合成视频、本地Flite英文测试语音和BGM，实际导入数据库，进行机器QC，写入明确TEST FIXTURE审核，建立对白候选与采用，冻结SRT和v3时间线，再通过持久Job及CPU Worker合成。未审核render时交付返回422；对测试render显式批准后完成文件生成与verify，MP4下载200且hash相符，Range读取206、1024字节、Content-Range正确。

成片规格：**8秒、320×180、24fps、192帧、H.264＋AAC，含烧录字幕和独立SRT**；文件大小538298字节，SHA-256为`f8fb792bf5e301ebd06f08bb17034a161f9ccd91f8f656a13b87274511744566`。证据包含MP4、SRT、manifest、ffprobe、1秒/5秒两帧和文件摘要。

这条链显式调用了`ReviewService.ensure_templates()`补足测试前提，否则缺模型清单的原生启动会触发BKT-08。交付包自身的人工/平台审核仍为PENDING，没有把测试批准升级为正式发布批准。输出验证了媒体后期和交付通路，不是《照骨灯》的AI视频，更不构成模型画质、配音自然度和角色一致性的验收。

## 6. 全部问题登记表与修复方向

下面每个ID都在后续专项附录中有源码位置、触发条件、预期/实际、影响、修复方案和验收标准。执行顺序见单独交付的《AI修复任务书》。

| ID | 级别 | 问题 | 证据层级 | Phase | 最小修复方向 |
|---|---|---|---|---:|---|
| BKT-01 | P1 | 锁文件漏必需pypdf，应用导入/首次启动失败 | 隔离锁环境实测 | 0 | 统一依赖锁，发行smoke导入main并启动新库 |
| BKT-02 | P2 | 常规测试隐含读取开发机模型清单 | 全量基线和条件复测 | 0 | 专用TEST_ONLY fixtures，普通逻辑不依赖机器文件 |
| BKT-03 | P2 | 默认check依赖仓库上级旧蓝图与清单 | 命令实测和源码 | 0 | 版本化规范或显式外部参数，默认check可移植 |
| BKT-04 | P2 | 严格mypy门禁仍有93条Windows目标诊断 | 静态工具实跑 | 0 | 按Protocol、工厂签名、类型收窄等根因修复 |
| BKT-05 | P2 | 维护门禁未闭合，包含过粗直接import规则 | 审计工具实跑 | 0 | 拆分超限组件，纯常量规则合理豁免 |
| BKT-06 | P3 | 维护报告输出到仓库外时写完又异常 | 命令实测 | 0 | 报告路径格式化支持绝对路径 |
| BKT-07 | P2 | README称默认不接实时Comfy，api:test却未过滤 | 配置/命令源码确认 | 0 | 统一安全默认脚本与显式硬件验收入口 |
| BKT-08 | P1 | 缺模型清单跳过内置审核模板初始化 | 真实API/新SQLite对照 | 0 | 必需初始化与可选模型同步独立执行 |
| BKT-09 | P2 | 传输UAT退出未恢复Comfy访问环境变量 | 独立上下文动态复现 | 0 | finally恢复原值或删除原本不存在的键 |
| BKT-10 | P2 | Linux已退出未回收的模型进程被误判存活，停止/回收报超时 | 真实子进程/Z状态/端口对照；Linux条件 | 3 | 存活判断排除僵尸，正确回收自有子进程，保留PID归属校验 |
| HTTP-01 | P2 | 重复创建项目返回500，幂等键未兑现 | 真实HTTP及独立复现 | 6 | 冲突409或幂等返回，保留原目录与唯一约束 |
| HTTP-02 | P1 | 创建镜头忽略URL项目归属 | 真实服务/API数据库断言 | 2 | 同事务校验project→episode归属再写入 |
| HTTP-03 | P2 | 模板复制丢项目默认时长，追加集变120秒 | 真实服务/API数据库断言 | 5 | 复制默认配置，保留各集已确定时长 |
| HTTP-04 | P2 | 不完整数据库ready仍返回HEALTHY | 真实API/不完整库实测 | 6 | 核对schema head与关键能力，未就绪503 |
| NP01 | P1 | 首章前正文遗漏却标FULL并允许应用 | 真实规划服务＋显式LLM夹具 | 2 | 按母本文本区间并集证明覆盖，保留序幕 |
| NP02 | P1 | 超60章/超24000字符只处理首批，无可执行续接 | 真实规划服务＋显式LLM夹具 | 2 | 持久游标、长章切窗、幂等续接、最终覆盖核验 |
| NP03 | P1 | 存活旧修订API仅改标题也丢导演字段 | 真实HTTP修改与落库核对；当前UI未挂载 | 2 | PATCH只覆盖显式字段，其余字段保持 |
| NP04 | P2 | 空白原稿500并留下坏预览会话 | 真实解析/导入/API | 1 | 非空校验先于READY持久化，失败回滚 |
| NP05 | P2 | DOCX软换行/tab丢失导致对白合并 | 最小合法DOCX解析实测 | 1 | 保留文本边界并版本化解析结果 |
| NP06 | P2 | EPUB嵌套块正文重复 | 最小合法EPUB解析实测 | 1 | 单次遍历文本节点，避免父子重复收集 |
| NP07 | P2 | EPUB合法父目录相对引用解析失败 | 最小合法EPUB解析实测 | 1 | 先规范化包内URI再做路径安全检查 |
| NP08 | P2 | 更改已确认正文范围被静默当重复请求 | 真实导入/确认/API | 1 | 幂等身份纳入范围，变更显式新版本或拒绝 |
| NP09 | P2 | 常见章节标题和单换行TXT识别受限 | 已实测的格式能力限制 | 1 | 补常见识别规则及可解释结构预览 |
| NP10 | P2 | 原文passage不复验母本，返回篡改内容带旧hash | 真实文件篡改对照 | 2 | 统一段落与passage的版本/hash校验 |
| NP11 | P2 | 负数与垃圾时长字符串被洗成合法正数 | 解析/服务实测 | 2 | 严格数值与单位语法，拒绝歧义输入 |
| SS-01 | P1 | FAILED恢复嵌套写事务自锁约10秒 | 真实SQLite状态复现 | 3 | 单事务复用retry实现 |
| SS-02 | P1 | 恢复租约/会话丢取消和暂停意图 | 真实状态机＋过期/重启夹具 | 3 | 共享恢复决策，停止意图优先 |
| SS-03 | P1 | 软删除任务仍可重试/复活并不可见 | 真实状态机复现 | 3 | 删除终态参与所有写入/claim/回执校验 |
| SS-04 | P1 | 项目文件变化后再次导出OUTPUT_CONFLICT | 真实项目包三次导出对照 | 5 | 包身份同时包含业务状态和文件清单hash |
| SS-05 | P1 | 项目包副本丢文稿业务记录/范围和项目默认时长 | 真实项目包往返 | 5 | 版本化导出域，重写ID并验证引用完整性 |
| SS-06 | P1 | artifact登记未约束任务目录/租约/取消生命周期 | 真实文件/数据库/API边界 | 4 | 普通登记校验归属，故障对账使用单独受控入口 |
| SS-07 | P2 | artifact直接下载不复验完整性 | 真实文件篡改/下载 | 4 | 不可变发布与完整性验证；保持已有晋级校验 |
| SS-08 | P2 | 畸形项目包JSON类型触发500 | 真实恶意格式测试夹具 | 5 | 解析前验证类型/结构/限制，稳定4xx |
| SS-09 | P2 | 前128候选资源阻塞导致后续CPU任务饿死 | 真实调度库＋模拟资源占用 | 3 | 过滤后限量或有界候选分页 |
| SS-10 | P2 | outbox未发送事件也预耗重试预算 | 真实投递逻辑＋本地HTTP接收器 | 3 | 实际开始发送才计次，释放未发批内事件 |
| MED-01 | P1 | Comfy同名输出覆盖，hash错配仍VERIFIED | 本地HTTP提供方夹具＋真实PNG/SQLite | 4 | attempt/node/output独立身份，原子校验登记 |
| MED-02 | P1 | 非循环音轨忽略end_us，声音串到后段 | 真实FFmpeg＋音频采样 | 4 | 所有音轨统一trim/时间基/delay |
| MED-03 | P1 | 24/30fps混合转场失败而预检放行 | 真实FFmpeg与同帧率对照 | 4 | 统一fps/timebase后转场，预检冻结渲染计划 |
| MED-04 | P1 | Vox语速/情绪存元数据但不传实际运行 | 运行时参数记录器＋真实音频后处理 | 4 | 声明并应用参数能力，不支持则明确禁用 |
| MED-05 | P2 | 同revision前置空白渲染2秒而OTIO3秒 | 真实FFmpeg/OTIO对照 | 4 | 统一时间原点，支持空白或显式拒绝 |
| MED-06 | P2 | 单引号工作目录导致烧字幕失败 | 真实FFmpeg多目录对照 | 4 | 安全相对滤镜路径或规范多层转义 |
| MED-07 | P2 | 合成中途失败留下完整partial视频 | 真实FFmpeg失败与目录检查 | 4 | 最外层finally和per-attempt临时目录 |
| MED-08 | P2 | 外置字幕已生成，交付页却仅可下载MP4 | 真实交付HTTP＋前端源码 | 5 | 整包ZIP及受控单文件下载 |
| MED-09 | P2 | OTIO/EDL静默丢转场和混音参数 | 真实导出结构对照，未打开桌面编辑器 | 5 | 导出损失清单，支持项编码或提供烘焙素材 |
| FE-01 | P1 | 晚到上传结果清空新粘贴原稿 | React/jsdom动态复现 | 1 | 异步请求身份与当前输入状态绑定 |
| FE-02 | P1 | 主要编辑器未接全局草稿保护 | 原稿/时间线动态；其他编辑器源码 | 1 | 按项目/文稿版本注册dirty和恢复草稿 |
| FE-03 | P2 | 规划重试/影响预览/应用失败无提示 | React/jsdom三个动态场景 | 1 | 可见错误和保留输入后的重试 |
| FE-04 | P2 | 读取失败被显示为没有数据 | React/jsdom动态＋相关源码 | 1 | 区分loading/empty/error |
| FE-05 | P1 | Dialog/Drawer重渲染把输入焦点拉回关闭按钮 | React/jsdom两个动态场景 | 1 | 焦点初始化只随打开生命周期 |
| FE-06 | P2 | 一次Esc同时关闭内外嵌套浮层 | React/jsdom动态复现 | 1 | 顶层独占Esc，独立焦点恢复 |
| FE-07 | P1 | 对白/字幕开关不进dirty，冻结服务器旧设置 | React/jsdom动态复现 | 4 | 完整草稿签名与冻结前保存 |
| FE-08 | P2 | 回到开头只移动游标，video.currentTime未变 | React/jsdom动态复现 | 4 | seek独立事件/播放器调用，真实浏览器补验 |
| FE-09 | P1 | HTTP LAN缺randomUUID时请求前抛错 | 能力缺失条件下真实客户端函数测试 | 6 | 受支持ID生成或明确环境要求；不是实机LAN验证 |
| FE-10 | P2 | QC项目级/分集级空选项被自动重选子项 | React/jsdom动态复现 | 6 | 范围状态显式建模，避免falsy默认覆盖 |
| FE-11 | P2 | 项目/交付/审核固定首批，缺完整分页入口 | 当前源码/契约确认 | 6 | 保留分页元数据、全局搜索、尾页可达 |
| FE-12 | P2 | READY会话启动失败缺直接重试原会话入口 | 当前源码/状态契约确认 | 6 | 提供原会话start重试，避免额外创建；已有绕行保留 |
| FE-13 | P2 | 空资产库首次创建失败错误位于未渲染分支 | 当前条件渲染源码确认 | 6 | 在可见分支显示错误、保留输入 |
| FE-14 | P2 | Visual Lab搜索Esc提示与行为不符，模态键盘不完整 | 当前源码确认 | 6 | 复用统一模态/焦点管理，实现提示动作 |

## 7. 修复依赖、历史数据与优先级

建议按8个阶段实施：Phase0干净启动和可重复测试；Phase1输入保存和版本化解析；Phase2全书覆盖、真实续接、编辑与归属；Phase3任务状态和调度；Phase4产物、混音和时间线；Phase5项目包与交付；Phase6错误合同、健康探针和UI收尾；Phase7真实Windows/GPU/浏览器完整验收。完整任务书逐阶段列了修改范围、实施策略和出口条件，可直接交给实现AI。

三类修改必须同时考虑历史数据。第一，修DOCX/EPUB解析不能直接覆盖旧extracted.txt：旧分集仍引用字符offset/hash，必须新增解析版本。第二，修全书覆盖和续接不能重新覆盖已经进入镜头制作的分集；必须记录已授权文本范围和已应用单元。第三，修产物身份和包协议不能简单重命名现有文件或丢掉旧审计记录；需要扫描历史错配并提供隔离/重新采集或明确迁移规则。修复方案应保存用户已确认内容，不靠清库解决。

工程上保留当前FastAPI/React/SQLite本地架构。优先建立共享的归属检查、状态转换和不可变产物协议，再修调用端；不要各页面各写一套“特殊处理”。补测试时把本次“坏行为确实发生”的诊断断言改成正确预期，不能因诊断脚本exit0就认为修复通过。mypy逐诊断按根因修，不全局ignore；缺模型用显式测试夹具，不假报真模型可用；人工审核不能以自动选中代替。

当前代码参考：[文稿解析](https://github.com/Qioooba/local_drama_studio/blob/8a63c604a1a13556dbe277d312ecf73bebb52883/apps/api/local_drama/application/documents.py)、[主应用初始化](https://github.com/Qioooba/local_drama_studio/blob/8a63c604a1a13556dbe277d312ecf73bebb52883/apps/api/local_drama/main.py)、[项目路由](https://github.com/Qioooba/local_drama_studio/blob/8a63c604a1a13556dbe277d312ecf73bebb52883/apps/api/local_drama/api/routes/projects.py)、[任务状态](https://github.com/Qioooba/local_drama_studio/blob/8a63c604a1a13556dbe277d312ecf73bebb52883/apps/api/local_drama/application/jobs.py)、[Comfy产物收集](https://github.com/Qioooba/local_drama_studio/blob/8a63c604a1a13556dbe277d312ecf73bebb52883/apps/api/local_drama/application/comfy_jobs.py)、[时间线合成](https://github.com/Qioooba/local_drama_studio/blob/8a63c604a1a13556dbe277d312ecf73bebb52883/apps/api/local_drama/application/timeline.py)、[外部编辑导出](https://github.com/Qioooba/local_drama_studio/blob/8a63c604a1a13556dbe277d312ecf73bebb52883/apps/api/local_drama/application/timeline_exports.py)。精准行号与复现入口在各专项附录中。

## 8. 不能省略的剩余验收

**真实浏览器：** 平台返回的是“安全检查不可用，无法取得审批决定，未做显式拒绝”，并要求fail closed。因此本轮未进入应用，没有浏览器页面截图，没有完成逐页点击、中文输入法、真实播放器seek、屏幕阅读器、多分辨率与缩放测试。没有更换浏览器/地址绕过访问控制。React/jsdom能证明部分状态和焦点问题，但不能证明像素布局正确。建议在可访问的真实浏览器中覆盖1920/1440/1280/1024/768/390 CSS px、短高度、125%/150%缩放、超长中文、100+实体和所有错误态，逐页/逐浮层留证。

**真实模型：** 未具备用户实际GPU、权重、已发布Profile与Comfy/Vox/LatentSync服务。图像与视频生成、角色一致性、中文语音/情绪/语速听感、口型、分段连续性、真实超分画质、显存与耗时、真模型取消等保留未验证。4个Comfy标记测试已尝试但前提不足，不能当成4个算法坏了。最少需要真实短篇完整一集，以及跨61章批次的长篇规划覆盖报告和选定分集的真实生成产物。

**Windows与发行：** 已完成Windows目标Go构建和Windows目标类型检查，未运行Windows安装器、桌面启动器、SAPI、服务注册、UAC、防火墙、离线更新与回滚实机。需要在干净Windows上只按README完成安装；缺模型仍能完成本地导入、审核和CPU后期。字体、盘符、中文/单引号目录必须真实验收。

**性能与极限：** 真实并发/强杀和短媒体边界已测，但没有长时间稳定性、长篇全片磁盘峰值、真实GPU温度/显存、多小时恢复、海量媒体搜索或云/局域网吞吐基准。列表大规模问题目前是源码/契约确认，不冒称已创建上万条数据压测。超长原稿、恶意格式、资源限制在有界合成输入中验证，仍有输入空间需要后续回归补齐。

**条件风险：** 可信LAN无认证模式的Host/Origin观察为R-01，详见状态附录；没有完成真实浏览器DNS rebinding攻击。部分本地AI子进程阻塞调用可能影响取消，当前只有源码风险，真实FFmpeg取消/超时已通过。二者均未额外加入确认缺陷数，也不据此宣称项目遭到外部攻击。

## 9. 交付材料、复跑与使用方式

1. 《深度测试报告》：本文件，含汇总、完整问题登记和7份专项全文。
2. 《AI修复任务书》：可直接交给实现AI的分阶段工程指令、迁移约束和验收门槛。
3. 《接口覆盖清单》：全部631操作，方法、路径、operationId、主HTTP结果与当前源码定位。
4. 《测试证据与复现脚本》ZIP：原始JSON/JUnit/日志、各测试脚本、前端14项复现、数据清单与合成媒体样本；不打包多GB临时数据库、虚拟环境、node_modules或历史截图。
5. 《合成素材后期链路测试_8秒》MP4：本轮真实CPU链的最终测试产物；明确不是小说AI短剧。

ZIP保留相对目录，阅读证据路径时从解压根目录定位。脚本中少数解释器/工作目录为本轮绝对路径，`README_证据与复跑说明.md`逐类说明如何改为目标checkout与独立输出目录；不是无需安装依赖的一键发行包。正式已有测试和完整源码从固定GitHub提交取得，ZIP不重复打包完整源码仓库。所有模拟模型/提供方和测试审批都有层级说明。

最终关单必须以：原缺陷可复现→最小修复→正确预期回归通过→相关正向链不回退→实际目标环境补验为顺序。只有未验证项仍清楚保留，报告才可用于可靠决策。

## 10. 专项全文索引

以下7份专项构成本报告完整实施依据；报告中的“本轮”“当前”均指上述固定提交和明确说明的环境。相同问题跨域引用只计一次，主登记表ID为准。



## 附录A：后端从零启动、自动化与质量门禁

> 审计固定版本：`8a63c604a1a13556dbe277d312ecf73bebb52883`。本节由后端测试子任务产生，完整后端用例均已实际尝试，原始结果、补充 fixture 复测与环境边界分别保留。所有测试使用本轮源码的 Git blob SHA 校验副本与新临时数据，不使用用户现有数据库，不改产品源码。

### 1. 当前结论

1. **严格按 README 的锁文件安装，后端无法启动。** 两份 requirements 锁文件漏掉顶层必需的 `pypdf`；主应用导入链就会 `ModuleNotFoundError`，并非仅点击 PDF 导入时才失败。发行包构建使用同一缺失的 runtime lock，现有浅层 smoke 又检查不到该问题。
2. 补足这个依赖后，新空库迁移可完成到 `0100_production_session_waiting_user`；数据库有 234 张表，`integrity_check=ok`、`foreign_key_check=[]`，WAL 正常；重复执行迁移成功。
3. 本轮收集到 **261 个实际测试模块、1,587 个参数展开后的测试用例**（测试目录共 263 个 Python 文件，另 2 个是 `__init__.py`/`conftest.py`）。安全常规集 1,583 个，4 个带 `comfyui` 标记，单独列为真实运行时相关用例。
4. **常规 1,583 用例：1,351 通过、230 失败、1 个 setup 错误、1 个跳过**。四片 JUnit 的 nodeid 无重复、并集完整；另 4 个 Comfy 标记用例均尝试，但在缺私有模型清单/服务的前提处失败，没有完成真实推理。
5. `compileall` 和 Ruff 通过；在独立输出目录重新生成 OpenAPI 与 TypeScript 客户端，内容与提交版本一致。OpenAPI 有 577 个路径；字节哈希的差异仅为 Windows CRLF 与 Linux LF，不能报成接口内容漂移。
6. 类型与维护门禁没有通过：mypy 在 Linux 上 105 错误/29 文件；显式按 Windows 类型平台分析后仍 **93 错误/27 文件**。维护审计触发 2 类硬门禁：一个 React 组件超过项目自己的 700 行阈值，以及 `domain/image_input_roles.py` 不满足项目的直接测试导入规则。
7. 普通自动化用例中多处依赖未随库分发的本机 `model_manifest.json`，即使禁止 ComfyUI 访问也会失败。需要将“测试环境不自足”与真实生成算法缺陷分开；不能把这些失败逐条算成不同产品 BUG，也不能为了变绿伪称已有模型。

### 2. 环境与可重现性

- Python：3.12.14；Linux x86_64。
- 公共已装依赖环境：`/workspace/scratch/0102188ff063/audit_quality_venv/bin/python`。
- FastAPI 0.141.1、SQLAlchemy 2.0.51、Pydantic 2.13.4、httpx 0.28.1、pytest 8.4.2，与仓库锁文件相符。
- 该环境额外具备 `pypdf 6.19.0`，所以它只能用于继续功能测试，**不能代表 README 的全新锁文件安装通过**。
- 为验证新安装，另外新建 `evidence/backend_tests/lock_venv`，只让锁文件规定的 38 个、已验证精确版本的发行包可见。通过只读符号链接复用既有安装包，不下载依赖、不修改共享环境、不人为拦截 import。包清单见 `lock_environment.json`。
- 新库：`evidence/backend_tests/new_database/data/local_drama.sqlite3`。
- 原始串行用例副本：`evidence/backend_tests/testrepo`。
- 并行用例副本：`evidence/backend_tests/repo_shard1` 至 `repo_shard4`。按已收集用例数贪心均分模块，各片原始数为 397、397、397、396；四片模块无重叠、并集为全部 261 个实际测试模块。不同片源码、默认存储根、pytest tmp 完全独立。
- 没有使用缓存数据库替换 migration，没有 patch 产品服务以让测试变绿。数据库迁移测试仍运行真实 Alembic 与真实 SQLite。

#### 关键命令

```bash
## 全套收集
PYTHONPATH=apps/api /workspace/scratch/0102188ff063/audit_quality_venv/bin/python -m pytest --collect-only -q

## 常规集；4个Comfy用例单列
PYTHONPATH=apps/api LOCAL_DRAMA_COMFY_ACCESS=disabled /workspace/scratch/0102188ff063/audit_quality_venv/bin/python -m pytest apps/api/tests -m 'not comfyui and not video_upscale_gpu' --basetemp=<独立目录> --junitxml=<结果.xml> --durations=35 -ra

## 新库与重复迁移（执行了两次）
PYTHONPATH=apps/api /workspace/scratch/0102188ff063/audit_quality_venv/bin/python scripts/migrate.py --database <独立目录>/local_drama.sqlite3

## Python静态与编译门禁
python -m compileall -q apps/api scripts
python -m ruff check apps/api/local_drama apps/api/tests scripts
## 工作目录 apps/api
python -m mypy local_drama
python -m mypy --platform win32 local_drama
```

并行执行的完整可复现程序为 `harness/backend_tests/run_shards.py`，分片清单为 `evidence/backend_tests/shards_manifest.json`。初始源码下载完成前，首轮串行运行曾因架构基线文档未落地产生 1 个环境瞬态失败；文件补齐后该模块单独复测 3/3 通过，完整四片从齐备的当前源码起跑。该瞬态不得作为产品缺陷。

### 3. 确认的问题与实施方案

#### BKT-01 / P1：必需依赖未进入锁文件，首次启动与发行运行时失败

**证据与代码位置**

- `README.md:151–156` 指示仅安装 `apps/api/requirements.lock` 与 `apps/api/requirements-runtime.lock`。
- `apps/api/pyproject.toml:10–18` 声明 `pypdf>=5,<7`，但两份锁文件全文均没有 pypdf。
- `apps/api/local_drama/application/documents.py:18` 顶层无条件执行 `from pypdf import PdfReader`。
- `packaging/common/build_release.py:209–226` 发行 app 目录依赖只来自 `requirements-runtime.lock`。
- `packaging/common/build_release.py:232–239` 的验证仅 `import alembic, fastapi, local_drama, sqlalchemy, uvicorn`，没有导入应用 `local_drama.main`。

**真实复现**

```bash
PYTHONPATH=apps/api evidence/backend_tests/lock_venv/bin/python -c 'import local_drama.main'
```

返回码 1，调用链为 `main.py → routes/asset_bible.py → infrastructure/service_composition.py → application/documents.py:18`，异常 `ModuleNotFoundError: No module named 'pypdf'`。同一隔离环境执行发行脚本现有浅层 import 检查却可以通过，因此该 gate 会漏检。

证据：`fresh_lock_startup.log`、`fresh_lock_packaging_smoke.log`、`lock_environment.json`；构建环境脚本：`harness/backend_tests/create_lock_env.py`。

**修复方案**

1. 从 pyproject 的运行时依赖生成/同步锁文件，将经过验证的 pypdf 精确版本纳入 runtime lock 与开发 lock，避免维护三份彼此漂移的手工清单。
2. 同步离线 wheelhouse 与 SBOM 清单，检查 Windows 私有 Python 和 Linux 私有 Python 的发行包都包含该依赖。
3. 将发行 smoke 提升为私有运行时导入 `local_drama.main`，并在新临时 instance 上执行迁移、创建 app、访问 health/contract。单纯 `import local_drama` 不能证明子模块可导入。
4. 在依赖变更门禁中检查所有 pyproject 必需依赖都存在于 runtime lock，选定版本满足 pyproject 范围。

**验收**

从无项目依赖的新 Python 3.12 环境，仅执行 README 的安装命令即可导入并启动 API/Worker；TXT/MD/DOCX/PDF/EPUB 导入都能进入相应解析流程；离线发行包 smoke 在没有系统 pypdf 的机器通过。不能用“开发机早已装过 pypdf”作为证据。

#### BKT-02 / P2：常规自动化测试依赖开发机清单，干净源码测试不可复现

**证据与触发**

- `apps/api/tests/conftest.py:17–35` 的 workspace 创建独立数据根，但没有提供测试用 model manifest。
- `apps/api/local_drama/config.py:261–267`（`manifest_path`）回退到仓库或仓库父目录的 `model_manifest.json`。
- `apps/api/tests/test_asset_bible.py:79–96` 在普通资产图片批处理测试 helper 中调用真实 `ProfileService(...).sync_manifest()`。
- `apps/api/local_drama/infrastructure/manifest.py:72–81` 在文件缺失时抛 `ManifestValidationError`。

独立运行 `test_asset_image_batch_submit_uses_independent_generation_jobs_and_replays`，真实新库迁移先通过，随后因缺少仓库父目录的 model_manifest 失败。完整 traceback 见 `manifest_failure_case.log`。类似模式还存在于 Profile、Workflow、Generation 等常规模块；最终 JUnit 已归并出 194 个直接清单失败与 7 个间接清单断言失败；另有 17 个失败暴露了 BKT-08 的真实启动初始化耦合，不能把所有缺清单影响都归为纯环境问题。

**修复方案**

1. `tests/fixtures` 中放不含权重、不代表真实 smoke 的最小合法合成清单，明确 `TEST_ONLY`，只提供测试需要的模型能力、节点信任和候选结构。
2. workspace fixture 通过 `Settings(model_manifest_override=...)` 显式指向合成清单；测试 helper 必须传同一 workspace，禁止静默创建另一套默认 Settings。
3. 真正检查开发机 H3 模型文件、ComfyUI 服务和真实媒体输出的用例保留明确 marker 与独立 UAT 命令；这些检查不得使用合成清单假冒运行时就绪。
4. CI 的普通 pytest 环境不带任何模型、不带机器私有清单，仍能跑完全部普通逻辑用例。

**验收**

全新 checkout + 运行时依赖即可跑常规集，运行中不读工作目录外的机器清单；标记真实硬件的用例能清楚说明先决条件。补 fixture 后再复测当前被环境阻挡的用例，剩余失败才进入业务 BUG 分类。

#### BKT-03 / P2：默认 check 首步硬依赖仓库外的旧蓝图

`scripts/check.ps1:16–17` 无条件执行 `scripts/g0_validate.py`。后者 `:10–12` 通过 `parents[2]` 把 ROOT 设到源码仓库上一级，要求 `LocalDramaStudio_Blueprint_v2` 与 `model_manifest.json`；`:21–27` 必須找到每个固定编号的 Markdown 文档，`:50–58` 又固定旧蓝图 FR/NFR/TC 数量。

本轮从干净源码执行立即 `RuntimeError: expected exactly one in-scope document for 00`，日志 `g0_validate.log`。这些蓝图不是当前源码随附的普通构建前提，README 的“完整检查”也没有说明外部目录必须存在。

修复：把代码、契约、迁移、单测、静态检查组成默认可移植 check；外部蓝图/真实模型审计使用显式 `--blueprint-root`/`--manifest` 或单独命令。需要的版本化规范应存放在仓库，不能靠上级目录路径暗约定。缺少可选真实环境时标 `NOT_CONFIGURED` 并指向 UAT，不伪造 PASS。

验收：任意目录的干净 checkout 可执行默认 check；不再要求上级目录恰好存在同名蓝图；指定真实蓝图的扩展检查仍严格验证。

#### BKT-04 / P2：当前源码未通过自己声明的严格类型门禁

实际结果：Linux `105 errors in 29 files (checked 460 source files)`；Windows 类型目标 `93 errors in 27 files`。差额 12 个来自 Windows ctypes 平台符号，这部分不能认定为 Windows 运行 BUG；93 个跨平台剩余错误仍使脚本退出 1。

代表性修复方向：

- 多处 `DatabaseUnitOfWork` 被传给声明要求具体 `Database` 的服务/工厂：应统一服务依赖的协议与组合根适配方式，避免只改一端签名导致类型边界永久失真。
- `LocalLLMClientProviderPort.client(profile_version_id)` 与实际 `LocalLLMService.client(model, *, profile_version_id=...)` 签名不同：协议明确关键字参数，组合根实现一致；检查调用方是否正确按名称传参。
- `Any | None` 收窄：先保存局部值、完成 `isinstance`/空值判定，再访问或转数值，避免反复 `dict.get` 让检查器无法证明安全。
- `local_llm.py:1059–1063` 复用 `connection` 变量混合 SQLite connection 与 provider 配置字典：使用表达用途的不同局部变量。
- 调整返回结构类型，使数值字段不被错误注成纯字符串；不要全局加 `ignore_missing_imports` 或一律 `# type: ignore`。

完整逐文件、逐行证据在 `mypy.log` / `mypy_win32.log`，分类计数在 `mypy_summary.json` / `mypy_win32_summary.json`。这些是类型/工程门禁问题，**不能把每一条静态诊断都宣传成已复现运行时 BUG**；例如 breakdown composition 已有运行时 dict 防护，本轮没有将对应 mypy 告警误报为 null 500。

验收：在明确支持的 Linux/Windows 两套目标上执行项目 strict 配置为 0 错误；Protocol 与工厂的类型签名一致；相应功能回归集不退化。

#### BKT-05 / P2：维护审计存在当前未闭合硬门禁

独立执行 `scripts/maintainability_audit.py --output audit_test_output/maintainability.json` 返回 BLOCKED。明细：

- `apps/web/src/features/profiles/ProfileConfigurationPanel.tsx` 中 `ProfileContractsTask` 为 744 行，超过项目自行设定的单组件 700 行门禁。
- `apps/api/local_drama/domain/image_input_roles.py` 是 23 个 domain 模块里唯一没有直接自动化测试 import 的模块。
- domain 的禁止依赖检查通过；142 条 application 直接依赖 infrastructure 与 32 条 route 警告只是报告中的架构存量，不属于本条硬失败。

修复应按现有职责边界提取 Profile 表单/能力合同编辑子组件与必要 hook，保留交互语义。`image_input_roles.py` 实际只有 8 行、两个纯常量，硬性要求每个常量文件必须被测试直接 import 是规则过粗；应让审计识别并豁免纯声明模块，角色约束继续由消费方的真实业务测试验证。不要只补一个 import 或复制常量值的断言来凑绿灯。此处检查方式只是静态直接 import，不是行覆盖率，不能从它推断“该模块完全没测”。

验收：维护脚本的 deterministic hard_failures 为空；需要 Windows/真实浏览器的 UAT 状态继续如实保留，不能靠静态绿灯替代。

#### BKT-06 / P3：维护审计自定义外部输出路径会在写完后异常

`scripts/maintainability_audit.py:273–276` 明确接受绝对 `--output` 并写入该地址，随后 `_relative(output)` 使用 `path.relative_to(ROOT)`，输出在仓库之外时抛 `ValueError`。本轮 JSON 已成功生成，却没有正常打印汇总或走原本门禁退出逻辑。

证据：`maintainability.log`；用仓库内输出目录重试后正常返回 BLOCKED。修复：输出显示函数对仓库外路径返回绝对路径，或直接 `str(output)`；保留 `--output` 原有语义。验收覆盖仓库内相对、仓库内绝对、仓库外绝对三种路径，文件与退出码均正确。

#### BKT-07 / P2：README 宣称默认不接实时 ComfyUI，但 api:test 未做排除

`README.md:186–187` 将 `pnpm run api:test` 注为“默认不连接实时 ComfyUI”。根 `package.json` 的 `api:test` 实际为 `python -m pytest apps/api/tests`，没有 `-m` 过滤；4 个实时 marker 用例因而被默认收集执行。只有 `scripts/test_api_safe.ps1` 才设禁访环境并排除 `comfyui`/`video_upscale_gpu`。

修复：默认 api:test 采用安全 marker 过滤，将真实模型测试单独暴露为 api:test:live；README 同步写明服务、文件、Windows/GPU 前提。保留显式真实集，不删除不方便的用例。

验收：默认命令不会访问未授权的实时 ComfyUI；普通测试计数与4个真实集分别可见，显式 live 命令才执行后者。

#### BKT-08 / P1：可选模型清单异常跳过必要审核模板，已有素材从待审列表消失

**代码与触发**

`apps/api/local_drama/main.py:126–144` 把本地 Profile 清单同步与审核模板初始化放在同一个 `try`；`:130` 的 `sync_manifest()` 先执行，`:132` 才执行 `ReviewService.ensure_templates()`。前者缺文件/格式损坏时被捕获，API 仍继续启动，后者被直接跳过。新库的审核模板于是为零。`application/reviews.py:231–249` 无法为媒体找到模板时抛 `REVIEW_TEMPLATE_NOT_FOUND`；v2 读模型则可能把这些媒体排除为“没有待审目标”。

**独立真实复现与对照**

证据不是从 pytest 断言推测：媒体子任务在独立新 SQLite、实际迁移、实际 FastAPI lifespan、实际 PNG 导入下完成了 API 对照。使用带 pypdf 的依赖环境，明确指向不存在的模型清单，没有模型 provider mock，也不修改产品代码。

| 同一素材的状态 | 原生首次启动，清单缺失 | 仅显式调用幂等审核模板初始化后，清单仍缺失 |
|---|---:|---:|
| 审核模板 | 0 | 6 |
| review-targets HTTP | 200 | 200 |
| 待审目标总数 | 0 | 1 |
| overview.pending_count | 0 | 1 |
| overview.next_action | OPEN_POST_EDIT | OPEN_REVIEW |

复现程序：`evidence/media_pipeline/probe_review_template_startup.py`；原始 API JSON：`evidence/media_pipeline/review_template_startup/results.json`，`confirmed=true`。这会实际阻断导入素材审核与成片审核交付，并在页面上伪装成“没有待审内容”。本条同时解释完整测试中 17 个模板/待审/交付相关失败，仅按一个根因计数。

**修复方案**

把必要数据库 bootstrap（包含审核内置模板）与可选运行时清单同步分开处理。先独立执行可重入的必要初始化，成功后再尝试 Profile 清单同步；模型未配置应只使模型能力 `NOT_CONFIGURED`，不能使素材审核失效。也可把固定种子写入受版本管理的迁移，但要保持已发布模板 append-only 的既有语义。必要种子失败应进入明确不可服务的 health/readiness 状态，不能继续返回看似正常的空审核清单。

**验收**

全新空库在没有 model_manifest、manifest 损坏、manifest 存在三种情况下，都能列出内置 6 个模板；真实导入 PNG/视频后待审数正确，可完成批准/撤回、正式渲染版本审核和交付；重复启动不增生重复模板、不覆盖历史审核模板版本。模型推理仍应在未配置时明确阻挡。

#### BKT-09 / P2：本地传输 UAT 的环境恢复不完整，退出后继续开启 Comfy 访问

`scripts/local_adapter_transport_windows_uat.py:133–149` 的 `_hostile_proxy_environment()` 保存并恢复代理环境，但在 `:137` 另设 `LOCAL_DRAMA_COMFY_ACCESS=enabled`，该键没有加入恢复集合。

本轮在独立进程复现：调用前 `disabled`，上下文内 `enabled`，退出后仍 `enabled`，证据 `transport_environment_leak.json`。这会改变同进程后续测试的禁访边界，使测试执行顺序影响行为；不等于已证明有真实外部请求泄漏。

修复时同时保存该键原值及是否存在，统一在 `finally` 原样恢复/删除；保留 UAT 内允许连接本次 loopback server 的语义。验收覆盖原值为 disabled、enabled、未设置，以及上下文抛异常四种情况；退出后环境与进入前完全相同。

实际传输探测另外验证了 loopback、代理环境隔离、重定向拒绝、错误脱敏等 5 项，全部通过，`transport_probe_results.json` 有逐项结果。Windows 主机检查在本轮 Linux 上失败属于平台前提；不把它误报为 HTTP transport 安全缺陷。

#### BKT-10 / P2：Linux 已退出的 adopted 进程被判仍存活，停止和模型切换假失败

**条件与证据**

`apps/api/local_drama/infrastructure/llama_server_manager.py:61–85` 在 POSIX 上只用 `os.kill(pid, 0)` 判断存活。Linux 已退出但父进程尚未 `wait()` 回收的进程是僵尸状态 `Z`，PID 仍存在，该检查返回 true。`:383–391` 的 adopted PID 停止和 `:450–464` 的错误 alias 回收都依赖这个条件，因而把“进程已退出”误报成 `LLAMA_SERVER_STOP_FAILED`。

复现使用真实 Python HTTP 子进程、真实信号和 loopback 端口，缩短 grace 为 0.35 秒，只替代模型本体，不 patch 进程管理产品代码。与仓库失败测试一样，所谓 orphan 仍是当前父进程的子进程；因此这里确认的是**父进程未及时回收时的 Linux 生命周期缺陷**，不是所有 Linux/Windows 停止都会失败，更没有执行真实 GPU 卸载。

| 场景 | 原子进程状态 | 原端口 | 原代码结果 |
|---|---|---|---|
| adopted PID 停止，父进程尚未 wait | Z | 已关闭 | 等待 0.3625 秒后 STOP_FAILED |
| 同上，另有父进程及时 wait | 已消失 | 已关闭 | 0.0205 秒成功 |
| 不匹配 alias 回收，父进程尚未 wait | Z | 已关闭 | 等待 0.3643 秒后 STOP_FAILED，新服务未启动 |
| 同上，另有父进程及时 wait | 旧 PID 已消失 | 新服务正常监听 | 0.1687 秒完成切换，后续正常停止 |

程序 `harness/backend_tests/reproduce_linux_adopted_pid.py`；证据 `linux_adopted_pid_probe/results.json`，两个错误与两个回收对照都稳定得到预期结果。适用场景包括同进程重建 manager、父进程延迟收尸，及没有合格 init/reaper 的容器。普通已持有 `Popen` 的分支会主动 `wait()`，本轮没有把它误判成同一缺陷。

**修复方案**

把平台进程状态分为 running、exited、missing；Linux 可优先使用 pidfd/poll 等进程退出通知，或在受控 fallback 中读取 `/proc/<pid>/stat` 并把 Z/X 视为已退出。只对确实由当前管理器拥有的子进程执行 wait；不能全局 `waitpid(-1)` 抢走其他服务的退出状态。继续执行端口释放检查，并保留 PID 文件/进程身份边界，避免把重新分配的 PID 当成同一模型进程。

**验收**

保留现有两条 adopted 测试，新增本轮四场景对照；未回收僵尸应识别为退出，错误 alias 能启动新服务，活着且不接受终止的进程仍正确超时报错。普通 Popen 管理分支、Windows Job Object 与真实 GPU 内存释放分别按对应平台验收。

### 4. 已通过验证的工程事实

- 全部已同步 Python 代码和脚本编译通过。
- Ruff 全范围 0 条诊断，`ruff.json=[]`。
- 新库和重复迁移通过；新库检查结果见 `new_database_state.json`。
- 当前生成器生成的 OpenAPI 与 TS client 内容一致，结果见 `generated_artifacts_check.json`；没有因为 CRLF 差异误报契约失同步。
- 架构债务基线补齐当前文件后，`test_architecture_debt_manifest.py` 为 3 passed，见 `architecture_retest.log`。
- “缺模型就绪”与常规逻辑校验必须区分：例如 `test_asset_image_batch_plan_skips_existing_hero_and_reports_missing_profile` 在无模型条件下通过，表明部分预检已能诚实表达缺能力；不是所有模块都一概失效。

### 5. 自动化结果、失败归因与测试边界

#### 5.1 不修改源码、不提供机器清单的完整原始基线

| 执行组 | 通过 | 失败 | setup 错误 | 跳过 | 实际用例数 | pytest 用时 |
|---|---:|---:|---:|---:|---:|---:|
| shard 1 | 328 | 68 | 1 | 0 | 397 | 808.56 秒 |
| shard 2 | 359 | 38 | 0 | 0 | 397 | 753.69 秒 |
| shard 3 | 336 | 59 | 0 | 1 | 396 | 674.67 秒 |
| shard 4 | 328 | 65 | 0 | 0 | 393 | 529.70 秒 |
| **常规完整基线** | **1,351** | **230** | **1** | **1** | **1,583** | 四片并行；不可相加当墙钟 |
| Comfy 标记单独尝试 | 0 | 4 | 0 | 0 | 4 | 缺配置，未到推理阶段 |

全量 collect 是 1,587；常规执行跳过的是一项 Windows SAPI 可用音色前提。`pytest_baseline_summary.json` 验证 1,583 唯一 nodeid、重复为零。`pytest_all_baseline_records.json` 保留每项状态、耗时与首条错误；完整 traceback 在四份 XML/log。原先额外串行副本在完整四片结束后终止，既不计入通过数，也不重复累计。4 个 Comfy 用例是“尝试执行后被前提阻挡”，绝不等同真实模型通过。

#### 5.2 所有 231 个失败/错误项均有根因分类

| 原始触发分类 | 数量 | 裁定 |
|---|---:|---|
| 直接读取缺失的本机 model_manifest | 194 | BKT-02，普通测试缺自足 fixture；不是 194 个独立产品 BUG |
| 清单缺失造成间接断言/空投影 | 7 | 先命中 MANIFEST_INVALID、422 或空模型投影；补静态 fixture 对照 |
| 缺模板、空待审或审核交付失败 | 17 | BKT-08，同一真实产品初始化耦合的下游表现 |
| 平台与机器配置假设 | 12 | Windows 凭据 2、Linux PID 等待 2（已确认 BKT-10）、SAPI 3、Windows SBOM 1、Windows UAT 1、F 盘配置 1、LAN 设置 2 |
| 临时 SQLite I/O 异常 | 1 | 独立新库复测通过；不据单次并行异常断言持久数据损坏 |
| **合计** | **231** | **230 failed + 1 error；没有“未分类”项** |

逐项映射：`baseline_failure_classification.json` / `.csv`，每个失败都有 nodeid、原始错误、类别、归因说明。分类描述原始失败最先触发的原因；例如补齐 manifest 后又出现 GPU/字体缺失，会作为后续前提继续记录，不能反向把原基线改写为 PASS。

#### 5.3 合成清单复测：只补测试前提，不模拟真实硬件通过

没有改动 baseline 副本。在额外独立副本提供明确 `TEST_ONLY` 合成 manifest：没有真实权重、没有可执行插件、模型绝对路径故意不存在、Comfy 访问仍禁用。初版只给静态能力结构；第二版补当前编译器要求的 loader asset 名称与可信节点名称。`static_compiler_runtime_boundary.json` 证明运行时 layout 仍为 BLOCKED、解析到真实模型数为 0。它只能验证普通逻辑和工作流静态编译。

自动挑选 194 个直接 manifest 失败复测；第二批补测模板相关用例、静态编译边界和模型投影。19 项模板/投影/瞬态对照中 **17 passed**，剩余两项分别在 GPU 容量缺失、Windows 水印字体不可用处被正确挡住；没有把两项误报为新增业务断言 BUG。25 项第二版静态编译/引用路径测试通过。最终 8 份补测 XML 以原始 baseline nodeid 去重，额外 **212 个**原先非 PASS 的用例出现 PASS；与原始通过项合并，共 **1,563 个不同常规节点曾出现 PASS**，尚有 20 个未出现 PASS（含原始 1 个 skip）。这是不同明确前提下的对照结果，**不是一次全套测试 1,563 passed，更不代表生产修复后全绿**。精确去重汇总、逐项补测历史与剩余节点在 `supplemental_replay_summary.json`。

初版合成 fixture 还缺 IMAGE_CONCEPT 候选，4 个一句话生图/视频测试停在该测试前提；这 4 项已经完整尝试，不能声称验证了真实图像生成。QUALITY 磁盘容量断言已裁定为 fixture 误选，详见下节，不新增业务 BUG。原 replay 1 曾在 40 个点后长时间无进展，本轮停止该部分并用 160 秒上限、35 秒 faulthandler 的两组隔离补跑取代；原 partial log 不算正式复测结果。其他三片 replay 保留原结果，避免全套无限重复。

#### 5.4 平台、工具与瞬态裁定

- **Windows 凭据断言**：测试只 patch `WindowsCredentialStore.put`，而 Linux 平台组合根按设计选择 `LinuxFileSecretStore`；不能把 remembered 列表为空算作无法保存秘密。API probe 的凭据存储名称使用真实适配器，当前测试的 Windows 名称预期不适用于 Linux。另 `local_llm.py:1885` 的一句话计划返回名称仍硬编码 Windows，是跨平台返回元数据待修正处；它不证明秘密未存储。
- **SBOM**：原隔离副本没有 node_modules 安装元数据。提供本轮实际前端依赖元数据后，从 284 个 NOASSERTION 降到 73；其中 69 是非目标平台可选依赖，余 4 个都是 Linux 未安装的 Windows 包（两版 `@esbuild/win32-x64`、`@rollup/rollup-win32-x64-gnu`、`@rollup/rollup-win32-x64-msvc`）。脚本目标固定 win32/x64。`sbom_target_environment.json` 保留名称和版本。Windows 发行 SBOM 仍须在完整目标环境做 FINAL 验收，不能本轮宣布完成供应链审计。
- **机器路径**：`test_local_ai_subprocess` 直接期待开发者 F 盘模型目录；无机器配置的源码环境应提供合成 Settings 或标本机 UAT。
- **SAPI 与字体**：Linux 不具备 Windows SAPI/字体，保留前提状态。FFmpeg 视频/音频生成与渲染实测通过的部分仍有效，不能外推成 Windows 语音或水印验收。
- **QUALITY 磁盘门禁**：合成清单排序改变后，`tests/test_generation_variants.py:169–172` 的 helper 使用 `list_profiles()[0]`，选中了 `VIDEO_FIRST_LAST_FRAME`，测试却写入 `I2V_VIDEO` binding。原整体 preflight 因能力缺失已经 BLOCKED，磁盘子项的 UNKNOWN/PASS 不表示可绕过提交。状态专项复制测试库、仅校正已发布能力为 `VIDEO_I2V` 后，磁盘立即按 4 takes × 10,000 = 40,000 字节对可用 20,000 字节正确 BLOCKED。证据 `evidence/state_security/disk_gate_adjudication.json`。修复测试应按 capability 精确选候选，避免第一个元素的顺序暗约定；本条不增加业务 BUG。
- **SQLite**：原始1项迁移 `disk I/O error` 独立复测通过；补充并行过程中另1项资产批处理出现 `database disk image is malformed`，原库事后 `integrity_check=ok`，该项单跑通过且第二版静态复测又通过。记录为本环境并行文件系统瞬态/未判明原因，未发现可稳定复现的数据库损坏。状态代理另有可稳定复现的事务锁定 BUG，按其证据单独报告，二者不混为一谈。
- **Linux adopted PID**：两项原始失败已用4个真实进程场景对照确认 BKT-10，有明确父进程未回收条件，不归为单纯环境不足，也不推断 Windows 产品停止故障。
- **LAN 配置**：两个原始测试都漏了现行 JSON 信任声明。小说审计代理做8个独立子进程对照，提供 `network.trusted_lan_unauthenticated=true` 后，from_env 的各根目录/并发限制及 work_root→Comfy 目录派生均正常；其中一项测试还硬编码 Windows `\\tools`，在 Linux 应使用 Path 拼接。`LOCAL_DRAMA_TRUSTED_LAN_UNAUTHENTICATED` 环境键确实未映射，但当前文档没有承诺该键，正式 JSON 配置路径工作正常，因此裁定为过时测试前提与跨平台路径假设，不新增 LAN 产品 BUG。证据 `evidence/lan_final_review/results.json`。修复测试使用显式 JSON 信任设置和平台正确路径，并把 JSON 与支持的环境选项文档写清；8个子进程对照不计入212个补测通过数。

#### 5.5 能证明什么、仍不能证明什么

本轮后端具备全测试库存、每个用例实际尝试记录、失败逐项归因、新库/重复迁移、类型/lint/编译/生成契约门禁、合成能力编译回归与真实 SQLite/FFmpeg 执行证据。通过的测试包含大量项目/资产/小说链路、并发状态、变体/工作流、审核/时间线/交付和异常输入逻辑；相应模拟 LLM、provider、GPU 的测试只证明接口与编排约定，不证明内容质量。

本轮没有真实模型权重、目标 Windows 桌面/SAPI、实际 GPU/ComfyUI、可用真实商业 LLM 账号；因此无法宣称真实小说生成内容质量、每个视频模型输出质量、Windows 安装器运行、长时间 GPU 稳定性、生产级超大项目性能全部通过。没有采集 Python 行/分支覆盖率；1,587 是测试案例库存与执行量，不是所有分支或631接口100%覆盖的证据。用户最终需进行的目标机器验收应列为明确矩阵，不能用合成数据勾成已验收。

### 6. 证据入口

所有当前证据均在 `evidence/backend_tests/`：环境、锁清单复现、fresh import log、迁移 log/状态、全量收集 log、四片测试日志/JUnit、Ruff/mypy、维护性审计、生成器差异检查。`harness/backend_tests/` 保存本轮环境可见性重建、SHA 校验同步和分片运行脚本。临时 testrepo/pytest 数据用于可重现性，不应把大量临时数据库直接打包给用户。


## 附录B：独立HTTP、项目边界与原生运行宿主

基准：`8a63c604a1a13556dbe277d312ecf73bebb52883`。本节由本轮实际调用与当前源码得出。

### 实际执行

从未创建过的独立目录迁移数据库，真实启动 Uvicorn，并经 loopback TCP 发送请求，未使用 ASGITransport、没有模拟模型。

- 第一次实际启动日志：`evidence/live_server.log`。不同工具会话的网络命名空间不能互通，因此正式 HTTP 测试在同一执行进程中启动服务器和客户程序。
- 正式运行：`harness/live_http_audit.py live_http_v2`。证据为 `evidence/live_http_v2/requests.jsonl`、`summary.json` 与 `assertion_adjudication.json`。
- 共171个请求，涵盖153个不同接口操作；136次为已建立真实项目/分集/镜头/原稿资源后的GET巡检。所有请求均完成，无连接超时。此数量不是全接口所有输入组合的覆盖率。
- 631个运行时OpenAPI操作逐一入册，其中GET234、POST361、PUT18、DELETE9、PATCH6、HEAD3，全部已匹配当前源码函数与行号；完整清单见`evidence/coverage/api_operations.json`与`.md`。
- 98个GET由于需要本轮未建立的媒体/生成版本/运行实例或专用查询条件，没有在独立HTTP巡检中调用。它们仍列在覆盖表中，不能标为实测通过。
- 手工定义35条状态码验收中，原始断言31通过、4不符。复核后其中3条是测试预期与产品状态机不一致：旧导入hash返回422并正确拒绝；DRAFT项目不允许pause/archive返回409。已逐条裁定，不能伪报3个产品故障。正确裁定为34条符合现有规则，1条确认产品故障（重复建项目500）。
- `/profiles`与`/profiles/manifest`返回503，明确指向全新环境没有本机`model_manifest.json`；这是启动配置/自包含测试链的问题，不能说模型接口都已正常。

### 经过实际验证的正常行为

接口契约头缺失被409拒绝；实例token缺失及非允许Origin被403拒绝；空项目请求被422拒绝。创建项目预检不会写数据，真实建项目201。创建分集、场景、镜头正常；版本号正确的标题更新成功，旧版本覆盖被409拒绝。UTF8原创小说及GBK小说上传201，正确读取916字符、7段、6章；正文分页、导入确认、重复确认正常。空字节文件、禁止扩展名、损坏PDF及缺失路径能拒绝。模板复制、项目包生成的正常请求成功。

在没有真实LLM方案的情况下，小说预检200且`ai.ready=false`，说明原因`PIPELINE_LLM_REQUIRED`；正式启动返回422阻挡，而不是产生一份假AI结果。此处实际小说链停在已导入/已确认/待模型配置。该行为正确，后续模拟LLM专项只能证明结构逻辑，不能证明实际AI生成质量。

### HTTP-01｜P2｜重复创建项目返回未处理500

**触发**：首次`POST /api/v1/projects`创建`audit_live_clock`成功；同一payload再次请求。

**实际**：500、`text/plain`、`Internal Server Error`。原有项目和目录仍在，没有证据表明本次重复请求删除了已有数据。

**代码**：`apps/api/local_drama/application/projects.py:132`先调用`build_project_tree`，再在事务中检查同名code；`infrastructure/filesystem/template.py:57-58`目录已存在即`FileExistsError`；`api/routes/projects.py:63-93`只转换`DomainRuleError`，异常越过统一错误合同。

**影响**：双击、重试、网络回放等普通用户操作变成服务器错误。当前API声明接收`Idempotency-Key`但实际删除该变量；此现状不能被视为幂等保证。

**具体修复**：先在事务/仓储层核实code唯一约束；目录冲突统一转换`PROJECT_CODE_EXISTS`或`PROJECT_ROOT_EXISTS`，给出409且保证不删除非本次创建目录；仍保留数据库唯一索引处理竞态。若要兑现幂等键，应保存请求摘要与既有项目ID，相同key同payload返回原结果，同key不同payload409；不要只靠UI禁用按钮。参考同文件`copy_as_template`已把`FileExistsError`转换为领域错误的做法，但状态码映射也应一并定义。

**验收**：连续提交相同code、同key重放、并发两个相同code、磁盘已有同名目录但无数据库行四组；分别得到稳定409或幂等结果、原目录完整、仅一个项目，没有500及孤立partial目录。

证据：`evidence/live_http_v2/summary.json`，二次独立复现`evidence/root_boundaries/results.json`首项。

### HTTP-02｜P1｜创建镜头忽略URL项目归属

**触发**：创建项目A及分集A1，再向`POST /projects/B/episodes/A1/shots`发请求；另以`not-a-project`作为project_id再发请求。

**实际**：两次均201，镜头写入A1；URL指定另一项目/不存在项目都不影响写入。

**代码**：`apps/api/local_drama/api/routes/projects.py:477-481`显式`del project_id`，再只按episode_id调用`ProjectService.create_shot`；`application/projects.py:999`未接收project_id。

**影响**：单用户本地产品仍会出现上下文串项目写入；这是项目一致性缺陷，不需夸大为已完成外网越权攻击。前端缓存混用ID、回退页面或客户端Bug可把镜头写到非当前项目。

**具体修复**：路由把project_id传给服务，服务在同一写事务中联结episodes→seasons→projects检查归属。参数不匹配返回`EPISODE_PROJECT_MISMATCH`409，缺失资源404，检查通过后才能INSERT。为同类带双重实体ID的写路由统一复用归属校验函数。

**验收**：项目A/A1正向201；项目B/A1及不存在项目/A1拒绝且镜头、revision、audit、outbox数量均不变；不能只检查HTTP响应不检查数据库。

证据：`harness/root_boundaries.py`与`evidence/root_boundaries/results.json`第3、4项。

### HTTP-03｜P2｜模板复制丢失项目默认时长

**触发**：将默认90000ms的项目复制为模板副本，在副本中追加分集且不指定时长。

**实际**：复制后旧分集仍90000ms，但副本项目`target_duration_ms=null`，追加分集变为120000ms。

**代码**：`application/projects.py:535-556`复制项目INSERT未包括`target_duration_ms`；同文件`1063-1071`追加分集时null退回固定默认120000ms。

**影响**：使用90秒、3分钟等模板的用户，新增一集突然变为2分钟。原有分集值正确会掩盖问题。

**具体修复**：复制项目时拷贝明确默认时长，同时保留各分集已解析的时长；列出“模板可复制配置”并用有类型的配置投影统一复用。升级修复现有null副本需提示来源和规则，不要反向推断第一集时长并无条件改历史数据。

**验收**：源项目90秒、分集各自60/90秒，复制后项目90秒、各集保持原值、追加分集继承90秒；另测默认120秒及24小时边界。

证据：`evidence/root_boundaries/results.json`第二项。项目包往返中的默认时长丢失是另一个实现入口，见状态/项目包章节，需同时修复。

### HTTP-04｜P2｜数据库不完整时就绪探针仍称健康

**触发**：创建仅有`alembic_version`表及`0001_bootstrap`记录的独立数据库，启动当前应用。

**实际**：启动同步记录缺表错误，但`GET /health/ready`返回200、`HEALTHY`、database=ok；随后`GET /projects`返回500。

**代码**：`api/routes/health.py:78-96`只验证能读取任意alembic版本记录，没有核对预期head、关键表/列；`main.py:134-149`启动同步异常记录后继续启动。

**边界**：正式Runtime Host若严格先迁移可降低暴露概率；本次证明的是直启API/迁移不完整时健康声明失真，不等于当前完整新库迁移失败。

**具体修复**：比较当前版本集合与发行迁移合同head，检测核心schema能力；将schema不兼容设置应用级readiness blocker。`live`继续表示进程存活，`ready`对不能服务业务返回503+结构化原因。不要为每个健康请求执行昂贵全库integrity_check，可在启动时缓存结果并在迁移维护后刷新。

**验收**：无库、仅版本表、旧head、多head、完整当前库五组；仅最后一种HEALTHY且projects可读。已有完整库的正常请求延迟不能显著增加。

证据：`evidence/root_boundaries/results.json`最后项。

### 本轮没有作出的结论

没有把无模型条件下的预检通过说成AI生成成功；没有把HTTP路由fallback200说成页面渲染成功；没有把API目录外的历史截图当作本轮UI实测；没有把所有已定义操作自动列为已测试通过。每一项范围与事实分别记录。

### 原生 Runtime Host / Launcher / 签名工具

使用Go1.25.0执行runtime-host现有全部测试，29个顶层测试通过（含子case共34个PASS事件）。Runtime Host、Desktop Launcher、release-sign三个模块分别编译Linux amd64和Windows amd64，6/6成功。覆盖的现有测试包含发行身份、签名拒绝/接受、包篡改、切换/回滚指针、归档路径穿越、残留锁、配置模板、模型路径保留和网络配置。详见`evidence/go_runtime/summary.json`、`tests.jsonl`。Windows为交叉编译验证，不是Windows安装、服务、UAC、防火墙或桌面启动器实机验收。


## 附录C：小说导入、全文覆盖、规划与应用

### 1. 结论与证据边界

本专项针对本轮 `repo` 中已由主任务逐文件核验 Git blob SHA 的源码执行。没有将上轮源码测试结果算作本轮证据。主任务取得完整源码后，本专项全部自定义场景已再次从空临时 SQLite 执行。未改生产代码；对抗测试使用原创/合成样本，另实际导入了仓库自带长篇；未使用用户本地数据库，未连接任何真实 LLM、ComfyUI、TTS 或 GPU。

专项共 **15 组自定义场景**：4 组通过、10 组确认缺陷、1 组确认功能限制；无测试脚本运行错误。这里是有目的的补洞测试，不是项目合格率。每组可能覆盖多种格式或条件，不把一条断言重复计成独立功能已通过。

最需要先修的是：

1. 首个被识别章节前的原稿段落被遗漏，覆盖报告仍写 `FULL`，质量报告 `READY`，且可应用正式分集。
2. 61 章原稿只处理前 60 章、超 24,000 字符的单章只覆盖开头；页面提示“续接”但没有可执行的继续分析路径。
3. 旧场次修订接口仅修改标题也会丢失原先的镜头/运镜/构图/光线/声音/连续性信息，并能成功应用到正式镜头。**现行主页面未挂载这个旧修订面板，故此条是存活 API 缺陷，不声称浏览器主流程已触发。**
4. 仅空白 TXT 上传返回 500，但已经写入 `PREVIEW_READY` 会话；重复上传仍复用坏会话。

主要产物：

- `harness/novel_pipeline_review.py`：可重复运行的自定义脚本。
- `evidence/novel_pipeline/results.json`：逐场景真实结果，明确测试层级。
- `evidence/novel_pipeline/manifest.json`：运行上下文、源码 SHA256、统计。
- `evidence/novel_pipeline/review-run.log`：完整本轮输出。
- `evidence/novel_pipeline/edited_scene_formal_output.json`：编辑后真正落入 `shot_revisions.fields_json` 的镜头字段。
- `evidence/novel_pipeline/domain_routes.json` / `.md`：11 个领域路由模块、105 条路由库存。**库存不等于这 105 条全部逐一做了黑盒测试。**
- `harness/novel_repo_sample.py`、`evidence/novel_pipeline/real_novel_import.json`、`real-novel-run.log`：仓库自带长篇原稿的真实导入补测，完全未 mock 模型。

执行命令：

```bash
/workspace/scratch/0102188ff063/audit_quality_venv/bin/python \
  harness/novel_pipeline_review.py repo evidence/novel_pipeline
```

测试虚拟环境包含额外安装的 `pypdf`；按 README 从零仅安装锁文件的依赖缺失由后端测试专项单列。不能用这里 PDF 上传 201 推导 README 全新安装成功。

格式对抗样本中 DOCX/EPUB 使用覆盖所测 XML/ZIP 结构的最小夹具，PDF 使用带文字层的生成夹具；它们用于定位本解析器的具体行为，不等于 Word/WPS/Calibre 全格式兼容性认证。本轮另有真实仓库 TXT 长篇导入，见下节。

#### 仓库真实长篇样本补测

对本轮已核验的 `照骨灯_凡人修仙原创长篇_约200分钟.txt` 另开一个空数据库执行真实上传、解析、提交、预检和启动尝试。文件 SHA256 为 `aadea09ebef821c0399c034c951c0261eff23ccc417a72c1dff4cce036c49d21`。

| 项目 | 实测 |
|---|---|
| 文件大小 | 86,609 字节 |
| 解析字符数 | 29,855 个 Unicode codepoint，不等同纯汉字数 |
| 结构 | 738 段、42 章 |
| 上传解析写入 | HTTP 201，0.591 秒 |
| 提交全文范围 | HTTP 200，0.064 秒 |
| 故事预检 | HTTP 200，0.165 秒，估计42集 |
| 解析全文与源文件标准化文本 | 完全相等，预览被截短未导致底层全文截断 |
| 结构单元大小 | 42个单元，每个563–1,355字符 |
| 无模型启动尝试 | HTTP 422，`PIPELINE_LLM_REQUIRED` |
| 失败后的状态 | 1 source/version/session；0 pipeline_runs、0 jobs |

上述时间为当前测试容器内单次实测，并非用户 Windows/GPU 性能承诺。本样本章节前 1–5 段由书名、体量说明、简介标题与两段梗概构成，分集单元从第6段开始；这再次显示程序自动排除了前置范围，但不能把这5段一概描述为正文章节内容丢失。NP01 的实际叙事遗漏由另一个带唯一序幕事件的针对性 fixture 证明。文件名“约200分钟”没有作为已生成片长证据，本次没有视频或音频生成。

### 2. 当前实现的数据流

当前主故事入口支持“上传原稿 / 已有原稿 / 粘贴正文”。代码中没有发现从一个主题直接原创生成整部小说的独立已挂载入口；一句话视频入口属于另一条视频创作流程。现有小说工作流是**已有小说原稿 → 改编规划 → 资产及逐集制作**。

1. `imports:upload` 将受限大小的上传落入工作目录，再交 `DocumentImportService`。TXT/Markdown 按 UTF-8-SIG、GB18030 顺序解码；DOCX 读 `word/document.xml`；PDF 用 pypdf 提取文字；EPUB 按 OPF spine 读正文。
2. 原始媒体被受控复制、SHA256 固定；解析正文另存 UTF-8，并存 `text_sha256`。建立 source document/version、导入预览会话和全文搜索索引。
3. 段落按“空行”拆分，索引用 Unicode codepoint，章节识别在段落内查标题。提交保存 `SOURCE_BODY_RANGE`，后续 `pipeline` 读取已确认范围。
4. `pipeline:start` 冻结源文本、范围、视觉风格、每集目标时长和授权快照，排入持久任务。真实大模型未配置时预检能明确返回 `PIPELINE_LLM_REQUIRED`。
5. `_episode_specs` 以章节或约 3,500 字符的段落组合构造分集单元；执行仅取前 60 个单元；真实 AI 提示词每单元只取 24,000 字符。分集提纲、实体观察和全剧记忆为逐阶段模型任务。
6. 草案包含规划、人物/场景/道具和故事记忆。当前 v3 全剧规划有意不提前生成逐镜内容，详细分场、镜头、对白、提示词推迟到逐集制作。
7. `apply-preview` 计算实际影响，`apply` 依据 revision 与 impact hash 写正式分集、故事记忆和资产。原稿来源随分集冻结；已进入制作的分集有保护逻辑。
8. 另一条存活的场次草案 API 提供人工修订、按场次应用，随后物化场景、镜头、对白及 DirectorIntent v3。现行 UI 已不挂载旧 `ScriptImportPanel` / `AIDraftReviewPanel`，属于 API 仍在、旧交互入口消失的状态。

### 3. 确认问题与具体修复方案

#### NP01 — P1：首章前正文被丢弃，覆盖率却是完整

**源码位置**：`apps/api/local_drama/application/pipeline_orchestrator.py:847` 的 `_episode_specs`，重点 `869-885`；同文件 `_source_coverage:920`，重点 `987-995`；质量规则 `81`。

**复现**：原稿为“序幕：独有关键事实……” + 空行 + “第一章 雨夜” + 空行 + 正文。从全文范围 1–3 启动。

**实际结果**：只有第 2–3 段进入分集输入，唯一序幕事实完全没交给生成函数；`covered_paragraph_count=2`、`authorized_paragraph_count=3`，但是 `status=FULL`、`resume=null`、质量 `READY`，应用预览 `can_apply=true`。真实数据库里成功应用的分集也仅绑定 2–3 段。纯领域 5 段案例同样出现 4/5 却 `FULL`。证据 `N09`、`N12/np_preface`。

**根因**：一旦有章节，分集循环只从第一个章节标题开始；没有将授权范围开头到首章前的区间并入任何单元。覆盖完整性仅检查已完成单元数和空 `unprocessed_ranges`，未检查授权区间的集合差。

**影响**：序章、引子、背景、首章前无标题正文会从整剧理解中消失，而且用户看到完整成功信号。后续人物关系、关键道具来源与结局呼应可能失真。

**实现方案**：构造章节单元前先覆盖授权区间全集；首章前非空正文可作为 `PROLOGUE` 单元，或明确并入首集。`_source_coverage` 统一按“授权区间 - 已处理区间”求补集；任何未覆盖字符/段落都要进入 `unprocessed_ranges`。`FULL` 必须同时满足集合差为空、没有截断窗口、没有未完成单元，不能仅比较数量。UI 若收到 `FULL` 但分母不等于分子，应呈现异常状态并触发重新评估。

**验收**：前置序幕、仅序幕、首章中间出现、用户选择从中间段开始、章节标题和正文同段等 fixtures；不允许任意有效授权段落从所有单元中消失；对任何 `FULL` 结果验证并集恰等于授权范围；已有全剧应用行为回归通过。

#### NP02 — P1：长篇只完成第一批，续接位置没有执行通道

**源码位置**：`pipeline_orchestrator.py:29-30`、`1083-1088`、`728-730`、`811-817`；`story_pipeline_ai.py:210`；前端 `apps/web/src/features/pipeline/OneClickPipelineWorkbench.tsx:466-507`。

**复现 A**：61 个规范中文章节，各两段，全文 122 段。模拟模型只替换生成本身，真实解析/任务状态/应用操作不替换。

**结果 A**：60 集 `SUCCEEDED`，完整覆盖 120/122 段，`resume_unit_number=61`、`reason=BATCH_EPISODE_LIMIT`。状态文案“本次原稿分析部分完成；请按续接位置继续”。`:resume` 返回 422 `PIPELINE_RESUME_UNSUPPORTED`；`:retry` 返回 409 `PIPELINE_STATE_INVALID`。可应用已生成的 60 集，正式库没有第 61 集。前端只展示续接信息，没有继续按钮；重试按钮只在 FAILED 状态出现。

**复现 B**：单章 28,813 字符，末尾放唯一情节。记录 24,000 字符覆盖、剩余 4,813；同样成功/部分完成且没有继续能力。真实提示词 `_episode_prompt` 明确截到 24,000；本测试未将模拟生成函数看到的完整 spec 当作真实模型收到的全文。

**根因**：有批上限和“续接游标描述”，但输入 schema、任务命令及执行器都未消费这个游标；`resume` 被直接禁用；成功的部分任务也不能 retry。

**影响**：较长小说的一键流程无法达到全书处理。反复“重新分析”会再次从前 60 单元/首 24,000 字符开始，浪费推理时间。不能把截断告警的存在等同为续接功能实现。

**实现方案**：新增明确的 `continue-analysis` 命令，输入 run_id、expected_revision、expected_source_sha256 和已保存的可信游标；续接只在同一源与同一授权范围中追加尚未处理区间，复用已完成提纲。对超长单章按 Unicode source offsets 创建多个有界窗口，不改原稿、不重新编号历史段落；综合单章窗口后再产生分集提纲。全剧汇总应合并完整已处理单元，避免每批重建导致覆盖已有名称/记忆。数据库保存每个窗口的输入 hash、状态、结果、请求身份；重复 continue 幂等。UI 在 PARTIAL 状态增加“继续剩余原稿”，显示剩余单元/字符与当前进行位置，直到真正 `FULL`。

**验收**：61、120、121 章和 24,001/48,000 字符单章都能分批完成；尾部唯一事实出现于实际传给模拟 transport 的输入；中断后只重跑未成功窗口；最后覆盖集合为全部授权原文。老任务无可靠游标时提示重建，不能猜范围。已应用第一批时后批追加应保留现有已制作分集，避免从 EP01 覆盖。

#### NP03 — P1（存活 API）：编辑标题会删除未修改的导演信息

**源码位置**：`apps/api/local_drama/application/breakdown_revisions.py:145-177`；`apps/api/local_drama/api/schemas/g3.py:146-160`；`apps/api/local_drama/application/breakdown_contracts.py:88-145`。

**测试层级**：人工构造符合现有字段形态的草案 fixture；真实 HTTP 修订 200 → 真实 HTTP 应用 200 → 查询正式 `shot_revisions.fields_json`。当前主页面没有挂载旧修订面板，因此不描述成已完成浏览器点击验证。

**复现**：原场次带 `location=古桥东岸,time=深夜,atmosphere=紧张,lighting=蓝色月光,props=[青灯]`；原镜头带 `shot_type=CLOSEUP,camera=推进,composition.preset=RIGHT_THIRD` 以及光线、声音、情绪、连续性。提交修订只改变标题为“河岸寻灯”，其他可编辑字段照抄。

**实际结果**：场次 location/time/atmosphere/lighting/props 全部丢失；镜头 camera/composition/continuity/emotion/lighting/shot_type/sound 全部丢失。正式输出是 `OTHER`、`STATIC`、`CENTER`、`sound_plan=null`，环境退化为“河岸寻灯”，青灯右手的连续性变成通用句。证据 `N15` 与 `edited_scene_formal_output.json`。

**根因**：`_validated_replacement` 重建只含少数 UI 字段的新 dict，不合并未修改的原字段。接口 schema 也没有表达这些字段，UI 无法在保存时补回来。

**实现方案**：对场次和每个稳定 shot_no 深拷贝原对象，然后仅覆盖被允许且明确提交的字段；未提交字段保持原值。使用 PATCH 语义或 `model_dump(exclude_unset=True)`，避免默认空值覆盖；如果保持 PUT，应定义完整契约并让 UI 支持全部字段。新增字段与 schema 升级要保留未识别的受信历史字段，不能破坏既有 DirectorIntent；同时禁止客户端覆盖源出处、冻结 revision 和系统状态。

**验收**：只改标题的修订，所有其余结构字段 deep-equal；仅改对白/时长/动作分别验证仅对应字段改变；应用后构图、运镜、环境、音效与原稿相同；修订历史仍不可变；stale revision、已应用场次再改都继续被拒绝。

#### NP04 — P2：仅空白原稿 500，并留下坏预览会话

**源码位置**：`documents.py:156-167`、`223-273`；响应契约 `api/schemas/g3.py:49-52`；上传 route `api/routes/imports.py:47-70`。

**复现**：上传 6 字节 `" \n\n\t \n"` 为 spaces.txt。

**实际结果**：HTTP 500 `Internal Server Error`。异常是 `ResponseValidationError`，`response.import.preview.paragraph_count` 为 0 但响应 schema 要求 ≥1。DB 已存在 `PREVIEW_READY` 和无段落 preview，重传同样内容复用旧坏会话。证据 `N04`。

**修复**：在复制/持久化权威记录之前，统一验证格式解析结果 `source_paragraphs(text)` 非空，否则返回 422 `DOCUMENT_TEXT_EMPTY`/`IMPORT_SOURCE_EMPTY`。不能只看上传字节数。TXT、空 DOCX、只有无效标签的 EPUB 全都适用。若后续存储或输出契约失败，应确保不留下“ready”会话；已有坏会话提供可识别的 INVALID/FAILED 状态及清理/重新导入通道。

**验收**：0 字节、仅空白、只有换行/制表符/Unicode 空格、空 DOCX 均 422，source_documents/source_document_versions/import_sessions 数量无增长；同样内容重复上传不会 500；有效一段文本仍成功。

#### NP05 — P2：DOCX 软换行/制表符消失，永久原稿对白边界被合并

**源码位置**：`documents.py:50-66`，尤其 `63` 只收集 `w:t`。

**实际复现**：原 Word 段落是“林默：打开门。” + `w:br` + “苏晚：等一下。” + `w:tab` + “门外有危险。”；解析成功 201，但输出变成一行“林默：打开门。苏晚：等一下。门外有危险。”，没有换行或制表符。证据 `N05`。

**修复**：DOCX 按文档顺序逐节点处理 `w:t`、`w:tab`、`w:br`/`w:cr`，表格按行/单元格保留清晰分隔；处理文本框/脚注可另列明确支持范围。避免同一内容因外层嵌套段落重复提取。不要简单全局拼接 text 节点。解析器版本变化按第 5 节方案生成新解析版本，不能覆盖旧 offsets。

**验收**：多 run、软换行、分页符、制表符、表格、中文说话人转换、多个段落 fixture；“谁说哪句话”与 Word 逻辑阅读顺序保持一致；原始 DOCX 字节不变，解析字符 offsets 可回查。

#### NP06 — P2：EPUB 嵌套块的正文被复制两遍

**源码位置**：`documents.py:85-98`。

**复现**：`<blockquote><p>唯一线索：青灯在桥下。</p></blockquote>` 和 `<li><p>苏晚说：我知道路。</p></li>`。

**结果**：每句话在解析原稿中出现两次。根因是遍历父 block 时 `itertext()` 包含子段落，再遍历子段落又加一次。不是模型重复，是原稿提取阶段已经重复。证据 `N06`。

**修复**：采用单次有序遍历，区分文本节点与块分隔；父块有可提取子块时不再次输出完整后代文本；仍保留混合文本前缀/尾缀。保留 blockquote/list 语义只影响分隔，不重复内容。不能用全局文本去重，因为小说真实重复对白必须保留。

**验收**：嵌套 li/p、blockquote/p、div/h1/p、行内 span/em、段落前后混合文本、真实重复句子；每个源文本节点恰好输出一次，阅读顺序保持。

#### NP07 — P2：EPUB 合法父目录相对引用被当作损坏文档

**源码位置**：`documents.py:109-121`。

**复现**：包文件 `OPS/package.opf`，manifest href `../Text/chapter.xhtml`，实际 ZIP 项 `Text/chapter.xhtml`。

**结果**：HTTP 422 `DOCUMENT_PARSE_FAILED`。代码拼成 `OPS/../Text/chapter.xhtml` 直接查 ZIP key，未规范化 URI 点段。证据 `N07`。

**修复**：按包内 POSIX URI 规则解析和规范化相对路径；去 fragment/query、解码 percent escape 后，规范化 `.`/`..`，验证仍在 ZIP 根内、非绝对路径、非外部网络 URI，再查 manifest 对应的 archive entry。使用 `PurePosixPath`/URI 解析而非宿主 OS 路径语义。

**验收**：普通引用、`./`、合法 `../`、带空格转义、中文路径、fragment 均可导入；逃出 ZIP 根、绝对路径和外链明确拒绝；Linux/Windows 输出一致。

#### NP08 — P2：更换已提交正文范围被静默视为重复请求

**源码位置**：`documents.py:180-203`、`579-590`。

**复现**：先 commit 1–3 段，再用同一 preview_hash commit 4–5 段；随后更名重导完全相同文件。

**结果**：第二次 200、`idempotent=true`，实际仍 1–3 段；更名重导仍复用同一已提交会话。不是安全的“相同命令重放”，而是不同请求内容未被识别。证据 `N08`。

**修复**：提交幂等身份必须包括最终规范化范围；已提交会话同范围重放返回原 receipt，异范围返回 409 `IMPORT_COMMIT_SCOPE_CONFLICT`，明确显示现有范围。提供创建新分析选择/新导入会话的明确命令，复用同一不可变 source version，但拥有新的授权范围，不需要重写原文。

**验收**：同范围相同请求可幂等；不同范围不再成功但无效果；新选择确实被后续 pipeline 的 authorized_source_scope 使用；已产生镜头的历史来源不变化。

#### NP09 — P2 功能限制：常见章节和单换行 TXT 无法正确结构化

**源码位置**：`apps/api/local_drama/domain/source_text.py:14`、`25-65`。

**实测**：`第一章雨夜`、`第1章：雨夜`、`序章 雨夜`、`Chapter 1 Rain` 均未识别；带空格 `第一章 雨夜` 与 `第001章 雨夜` 可识别。连续单换行的“两章正文”只有一个 canonical paragraph，章节只留下第一章。证据 `N10`。

**影响**：大量网络小说 TXT 的结构预览会退化为一个大段，用户无法按章节选择；进一步触发单章字符上限。空行段落本身是现有明确设计，故此条归为导入能力限制，不能说每个无空行文件都是数据丢失；但多章结构确实不可用。

**修复**：增加版本化结构识别策略：兼容紧接标题/冒号、序章/楔子/尾声以及可选英文标题；以有限正则识别明确标题，而非任意“第”字。若整稿几乎无空行，可在导入预览中让用户选择“按换行拆段 / 保持原段落”，并先展示章节清单。结构索引改变必须创建新解析/结构版本，不能让既有 offsets 漂移。

**验收**：真实不同 TXT 排版矩阵；正文内“第一章”叙述不能误作标题；连续空行/CRLF/emoji 保持 offset 不变量；同一解析版本重新导入结果一致。

#### NP10 — P2：原文引用接口不校验完整母本，和段落接口给出矛盾结果

**源码位置**：`documents.py:417-461`，特别 `446` 读片段后直接返回旧 `source_text_sha256`；对照段落 API `530-541` 的哈希校验。

**触发条件**：解析后的磁盘文本发生外部改写/损坏。本次仅在临时 fixture 中模拟，未改用户文件。

**结果**：段落 API 返回 `IMPORT_EXTRACTED_TEXT_CHANGED`；同源 passage API 却 200 返回“被改写的错误证据”，同时返回数据库中旧母本 hash。证据 `N11`。

**修复**：统一使用带完整性校验的 source reader；允许在文件 size/mtime 等元信息未变时复用上次完整 hash 校验，变更则重算并拒绝。不能只对片段计算 hash 后声称仍来自已冻结原文。用 content-addressed 文件、只读权限减少误改，但最终读取仍需一致性的明确验证。

**验收**：未改文件正常分页；截断、同长改写、追加、缺失返回一致错误；源码哈希和 UI 证据保持同一版本；不因大文档每次引用无条件全文件重复 hash 导致明显卡顿。

#### NP11 — P2：字符串镜头时长清洗过度，把负数与垃圾当合法时长

**源码位置**：`apps/api/local_drama/application/breakdown_contracts.py:149-172`，核心 `152`。

**实测**：数字 `-5` 被拒绝，而字符串 `"-5"` 得到 `5.0`；`"-1.2s"` 得到 `1.2`；`"abc10xyz"` 得到 `10.0`；`"1e1"` 得到 `11.0`。正常 `5`/`"5秒"` 成功；bool、None、0、16 拒绝。证据 `N14`。

**影响**：模型输出格式异常时，校验函数自行改变数值，时间线和目标时长可能偏离。用户从 HTTP 数值表单输入负数仍被 schema 拒绝，因此不是所有 UI 负数都能绕过；缺陷发生在共享生成/服务层接受字符串的路径。

**修复**：先严格数值 parse；如允许单位，只允许完整匹配“有限数值 + 可选 s/秒”的格式，符号与指数要按数值语义处理或明确拒绝。对 `NaN`/Infinity/bool 保持拒绝；不删除任意非数字字符来猜时长。单位 ms 如不支持则拒绝，不得误作秒。

**验收**：上述表格全部回归；负字符串永远不变正数；`1e1` 要么明确 10 要么明确拒绝；无自动改变错误请求；1 和15边界成功，0.999和15.001拒绝。

### 4. 领域覆盖矩阵与尚不能得出的结论

| 功能/条件 | 本轮证据 | 结果/边界 |
|---|---|---|
| 从空 DB 建项目/目录/迁移 | 自定义 harness，每次新临时根 | 成功；重复项目代码异常由主任务单列 |
| TXT UTF8/UTF8 BOM/GBK/GB18030 | N01 真实 HTTP → commit → passage | 成功，读取文本相等 |
| Markdown 两种扩展名 | N01 | 201 成功 |
| 普通 DOCX | N01 | 201，段落正确 |
| DOCX 软换行/制表符 | N05 | NP05 |
| 文本层 PDF | N01 | 201，正文提取成功；依赖安装问题另列 |
| 无文字 PDF/损坏 PDF | N03 | 422，明确 OCR/解析错误 |
| 普通 EPUB | N01 | 201，章标题+正文可取 |
| EPUB 嵌套正文 | N06 | NP06 |
| EPUB 父目录相对路径 | N07 | NP07 |
| 0字节/不支持扩展/坏DOCX/坏EPUB | N03 | 422，无伪成功 |
| 仅空白文档 | N04 | NP04，已持久化后500 |
| CRLF/CR标准化，emoji Unicode offsets | N02 | 通过 |
| 过期 preview hash/超范围段号 | N02 | 正确拒绝 |
| 同文件去重 | N01/N08 | 原始去重有效；不同范围命令 NP08 |
| 原稿/项目绑定 | N13 | 跨项目 source 被拒绝 |
| 来源冲突/短正文/无正文/非法时长 | N13 | schema 422 |
| 未配置真实模型的预检 | N13 | ready=false，明确原因 |
| 全剧规划任务与草案/正式分集 | N12 | 真编排/SQLite，生成替换为明确测试 fixture |
| 首章前正文覆盖 | N09/N12 | NP01 |
| 61章/超长章 | N12 | NP02；不是全书跑通 |
| 章节识别与单换行原稿 | N10 | NP09 |
| 段落证据与磁盘完整性 | N11 | 段落校验通过，passage缺失 NP10 |
| 场次人工修订→正式镜头 | N15 | 真实HTTP、fixture草案；NP03 |
| 共享镜头时长规则 | N14 | NP11；null composition防护正常 |
| 创作记忆版本/比较/恢复 | 已读 creative_entries 路由与 test_creative_entries | 既有测试归主任务全量执行结果；本专项不单称全绿 |
| 模型不完整JSON修复/检查点/调用数 | 已读 story_pipeline_ai 与同名单测 | 既有模拟测试；不等于真实模型语义正确 |
| 拆解草案部分应用/重复/跨项目/非法目标 | 已读 test_breakdown_apply 全部20项 | 主任务全量执行；本专项加做编辑字段保留缺口 |
| 拆解原文grounding/重复场景 | 已读 local_llm 验证与对应单测 | 主任务全量；未证明真实模型不会幻觉 |
| 分集来源A在导入B后保持/来源污染阻止 | 已读 test_episode_source_binding 5项 | 主任务全量；本专项真实跨项目拒绝已测 |
| 分镜批次计划/提交/冲突 | 已读 test_storyboard_generation_batches 3项 | 主任务全量；真实GPU生成不可测 |
| 长篇改编计划v2/manifest/分析DAG/落地 | 已读 adaptation_plans 路由及测试 | 主任务全量；现行主入口与旧计划页可达性由UI专项说明 |
| 小说人物/场景/道具语义提取质量 | 只明确模拟 fixture 和源码校验 | 本环境无真实LLM，不能认定提取齐全/准确 |
| 真生图/视频/配音/整集成片 | 由媒体专项测试可运行部分 | 无用户GPU与模型，不宣称真实生成成功 |
| 页面视觉/布局/点击 | UI专项承担 | 本专项未使用浏览器，只有源码交叉核验 |

### 5. 推荐实施阶段

#### Phase A：保证原稿完整与输出不丢失

先修 NP01、NP02、NP03。把“覆盖集合等于授权集合”和“编辑未提交字段不变”作为合并前自动化不变量。不要先通过增大 60/24,000 常量临时掩盖缺口，模型上下文和恢复问题仍在。

#### Phase B：稳定导入与可恢复用户操作

修 NP04、NP05、NP06、NP07、NP08、NP09。抽出有版本的统一提取/结构服务，持久化 `parser_version` 与 `structure_version`。

**特别注意历史兼容**：目前同一原始文件按 raw SHA 去重复用 source version；即使提升 `SOURCE_STRUCTURE_VERSION`，新提取文本若不同，`documents.py:215-219` 仍会因旧解析文件哈希不同而拒绝覆盖。不能只修解析函数然后要求用户重导。正确做法是在同一不可变原文件上建立新解析版本和独立 extracted 文件，例如 `<raw-sha>.<parser-version>.extracted.txt`，保留旧版本及所有旧分集来源/offsets，新增运行使用新版本。版本迁移/重解析动作需明确告诉用户会新建分析来源，不覆盖已有制作证据。

#### Phase C：收紧接口契约与证据一致性

修 NP10、NP11；统一输入/输出校验和服务层规则；增加结构读取缓存避免大文件反复解析。梳理旧 UI 未挂载但路由仍存活的入口，只保留明确支持路径，或补齐可达交互和维护责任；不能让用户以为页面里提供的能力与代码里的全部功能相同。

#### Phase D：用户真实环境验收

在真实 Windows + 当前模型配置上，用短篇、中篇、长篇三份授权测试小说跑“导入 → 全书完整覆盖 → 检查人物/场景/道具 → 逐集分镜 → 实际媒体 → 合成 → 人工审阅”。记录真实推理请求、模型版本/量化、输入覆盖、耗时、峰值显存、失败重试、最终媒体元数据。不以 mocked `generation_mode=LLM_STAGED` 或存在伪图片文件作为真实生成证据。

本专项已把能在本环境执行的文本、HTTP、SQLite、解析、编排和编辑复现做实；真实模型语义与整片观看质量仍需上述环境验收，不能保证“没有任何遗漏或缺陷”。


## 附录D：任务、状态、项目包与安全边界

### 1. 结论与证据基准

基准提交：`8a63c604a1a13556dbe277d312ecf73bebb52883`。本分工没有修改生产代码。10 个被引用核心文件已逐字节计算 Git blob SHA-1，与该提交 tree 的 SHA 一致，记录于 `evidence/state_security/source_evidence.json`。

本次新增 **26 个独立场景**：**13 个异常场景、12 个通过场景、1 个带前提的安全边界观察**。13 个异常场景归并为下面 **10 个修复条目**；同一根因的多个触发方式没有重复算作多个 BUG。仓库自带 pytest 的运行与总量由后端测试分工统一统计，本报告不把它们再次加到新增场景数中。

最需要先处理的是：

1. 失败任务点击“恢复”会发生 SQLite 自锁，实测等待 10.040 秒后报 `database is locked`。
2. 已经取消或暂停的任务，在租约过期、worker 停止后会被自动重新入队。
3. 软删除任务仍可被重试/恢复并执行，任务列表却看不到它。
4. 小说项目包导入副本会遗漏已提交文稿的业务记录、正文范围与项目目标时长；原 TXT 文件字节仍在包内，不能误写为原文文件完全丢失。
5. 任务产物可以登记到其他项目的任务上；文件被替换后直接下载仍保持 `VERIFIED` 标记。

正向测试也给出明确结果：24 次并发 claim 没有重复领取；24 次相同幂等键提交只生成 1 个 Job；12 个不同 worker 同时竞争跨 CPU/Comfy 通道的单 GPU 任务，只产生 1 个活动 GPU lease。真正向隔离子进程发送 `SIGKILL` 后，普通未取消任务能在租约到期后恢复为第 2 次尝试；未提交 SQLite 事务被杀后正确回滚；WAL 在线备份能包含最新已提交数据。

#### 执行环境与边界

- Python 3.12，复用 `/workspace/scratch/0102188ff063/audit_quality_venv/bin/python`，未升级依赖。
- 每个场景使用新建合成项目和独立 SQLite 库。`LOCAL_DRAMA_COMFY_ACCESS=disabled`，`LOCAL_DRAMA_EMBEDDED_WORKER=0`。
- 唯一网络服务是本机临时 HTTP webhook 接收器，所有请求只到 `127.0.0.1`；未调用任何外部模型、收费接口或用户真实数据库。
- worker 崩溃正向测试使用真实子进程和 `SIGKILL(-9)`；取消/暂停故障矩阵通过把合成租约设置为过去时间、调用停止 session 来可重复注入故障，二者证据类型已区分。
- 没有 GPU/Windows 桌面，因此未验证真正 3090 Ti 驱动、Windows Job Object、凭据管理器、NTFS junction 和真实模型运行中的强制终止。模拟 GPU lease 通过不等于真实显卡推理通过。

### 2. 新增场景覆盖矩阵

| 场景 | 结果 | 关键观测 | 证据文件 |
|---|---|---|---|
| FAILED Job 调用 resume | 异常 | 10.040 秒；OperationalError；仍 FAILED | state_reproduction_results.json |
| cancel 后 job lease 过期 | 异常 | CANCEL_REQUESTED→QUEUED→attempt 2 | 同上 |
| cancel 后 worker session 停止 | 异常 | CANCEL_REQUESTED→QUEUED，并再次 claim | 同上 |
| pause 后 worker session 停止 | 异常 | PAUSED→QUEUED，并再次 claim | 同上 |
| 删除 FAILED 后 retry | 异常 | 被 claim；get=JOB_NOT_FOUND；可见列表 0 | 同上 |
| pause→resume pending→delete→旧 attempt 完成 | 异常 | 隐藏任务变 QUEUED 并再次 claim | 同上 |
| cancel 与晚到 success | 通过 | cancel 优先，结果 CANCELLED | 同上 |
| pause/resume 等待旧 attempt 释放 | 通过 | 结算前不重新 claim，结算后可领取 | 同上 |
| 24 次并发 claim | 通过 | 24 个不同 Job，integrity=ok | 同上 |
| 24 次并发相同幂等键 | 通过 | 只有 1 个 Job ID | 同上 |
| 首次导出、原样重导出、添加许可文件后导出 | 异常 | 前两次通过；第三次 OUTPUT_CONFLICT | boundary_reproduction_results.json |
| 项目包 manifest/state 为 JSON 数组 | 异常 | 未捕获 AttributeError | 同上 |
| 项目包路径穿越/绝对路径/Windows 保留名称 | 通过 | 4 种输入全部 PATH_INVALID | 同上 |
| artifact 跨项目归属、取消后登记、文件替换 | 异常 | 全被接受；SHA 不符仍 VERIFIED 可下载 | 同上 |
| 文件根目录穿越、绝对路径、symlink | 通过 | 4 种输入全部 PATH_ESCAPE | 同上 |
| 前 128 个任务等待 GPU，后续 CPU 任务可运行 | 异常 | mixed worker 返回 None；CPU-only 可领取 | 同上 |
| SQLite 在线备份包含 WAL | 通过 | 最新已提交值保留，integrity=ok | 同上 |
| webhook 批量前项失败、后项尚未实际发送 | 异常 | 后项真实 POST 1 次，计数 6，进 DEAD_LETTER | 同上 |
| LAN 未配置 Host 同源请求 | 边界观察 | GET 可取 token；同 Host 写请求成功 | 同上 |
| 小说导入→正文范围提交→项目包副本往返 | 异常 | 原项目文稿 1、副本 0；目标时长 180000→NULL | roundtrip_crash_results.json |
| 错误 JSON 类型项目包经 HTTP 上传 | 异常 | HTTP 500；临时文件清理通过 | 同上 |
| 真实 SIGKILL 后新进程服务恢复领取 | 通过 | 退出 -9，attempt 2，旧 lease token 拒绝 | 同上 |
| 真实 SIGKILL 未提交 SQLite 事务 | 通过 | 原值保留，integrity=ok | 同上 |
| 12 worker 跨 CPU/Comfy 竞争单 GPU | 通过 | 1 个 claim，1 个活动 lease | positive_controls.json |
| 模拟密钥公开元数据与数据库审计脱敏 | 通过 | 公共响应/SQLite dump 不含完整测试密钥 | 同上 |
| 明示长度超限、无长度流式超限、空文稿 | 通过 | 全部 422；无导入会话；临时文件为 0 | 同上 |

### 3. 问题清单、根因与可执行修复

#### SS-01 — P1：失败任务“恢复”嵌套写事务，造成自锁

**触发**：任务已经 FAILED、NEEDS_ATTENTION 或 ORPHANED，用户调用 `POST /api/v1/jobs/{id}:resume`，或批量恢复命中这些状态。

**实际验证**：对普通 CPU_TEST 任务 claim 后 `complete(success=False, retryable=False)`，再调用 `resume`。10.040 秒后出现 `sqlite3.OperationalError: database is locked`，任务仍是 FAILED。

**定位**：`application/jobs.py:950-989` 的 resume 从 952 行开始持有 `Database.transaction()`；988-989 行在其内部调用 `self.retry(...)`；`retry` 的 884-910 行再次创建连接并启动写事务。`infrastructure/database/sqlite.py:16-29` 的 `BEGIN IMMEDIATE` 与 10 秒 busy timeout 使后一个连接一直等待前一个连接，而前一个调用又等待后一个返回。`api/routes/jobs.py:129-136` 直接调用该同步服务。

**影响**：正常错误恢复操作不可用。该 API 路由是 async 函数但内部执行同步阻塞调用，单事件循环可能在等待期间延迟其他请求；全服务延迟的具体数值需要另行并发 HTTP 测量，不能把本次服务调用耗时直接说成所有客户端均卡 10 秒。

**修复**：提取 `_retry_in_transaction(connection, row, ...)`，由 resume/retry 复用同一事务中的验证、状态更新与 outbox 写入。也可在事务外判断后调用 retry，但要让 retry 原子复验最新状态，避免先查后写竞态。不要通过调小 timeout、重试捕获或改用 DEFERRED 掩盖自锁。

**验收**：FAILED/NEEDS_ATTENTION/ORPHANED 各自 resume 在本地数据库正常负载下小于 500ms；同一任务只出现一次有效 requeue 事件；两个并发 resume 返回幂等结果或稳定 409；API 在操作期间仍响应健康检查。

#### SS-02 — P1：故障恢复丢失取消、暂停意图

**触发与实际**：

- claim→cancel→租约过期→`JobService.reconcile`：从 CANCEL_REQUESTED 变回 QUEUED，随后成功领取 attempt 2。
- claim(绑定 WorkerSession)→cancel→session.stop→session.reconcile：同样再次入队。
- claim(绑定 WorkerSession)→pause→session.stop→session.reconcile：PAUSED 变回 QUEUED。

**定位**：`jobs.py:1154-1187` 仅特别处理 PAUSED，未处理 CANCEL_REQUESTED；`worker_sessions.py:305-329` 查出的行不含 `j.state`/恢复意图，统一按 provider 是否受理和次数选择 QUEUED/NEEDS_ATTENTION。

**影响**：重启 API、关闭 worker、异常退出或机器恢复后，用户已经明确停下的生成任务可能继续执行。真实模型重跑和收费不是本次实际执行结果，但属于这些状态变化接入真实 provider 后的直接业务风险。

**修复**：让租约恢复、session 恢复、完成回执共用同一套状态决策函数；至少把 `state`、`cancel_requested_at`、`next_run_at`、`deleted_at` 与 provider 受理事实带入判断。未受理的 CANCEL_REQUESTED 应收敛为 CANCELLED；没有恢复意图的 PAUSED 应保持 PAUSED；只有用户已明确要求恢复且旧 attempt 已结算时才 QUEUED。外部已受理/状态未知时继续对账并保留用户取消/暂停意图，不能通过重新入队试探结果。资源释放与终态事件要在同一事务中提交。

**验收**：覆盖 queued/claimed/running、取消/暂停/恢复等待、未受理/已受理/受理未知、lease 过期/session 停止/进程被杀的组合。取消和无恢复意图的暂停在重启后绝不被 claim；晚到成功不得恢复生产。当前普通任务 SIGKILL 自动恢复通过的行为要保留。

#### SS-03 — P1：软删除任务可以重新执行，形成用户不可见的后台任务

**触发 A**：FAILED→delete→retry→claim。实际 retry 返回 QUEUED，新的 worker 成功领取；同一 ID 的 get_job 却报 JOB_NOT_FOUND，列表里没有任何任务。

**触发 B**：RUNNING→pause→resume（旧 attempt 尚未结算）→delete→旧 attempt complete。删除成功，complete 把任务重新变成 QUEUED，新的 worker 又能领取，但列表仍是 0。

**定位**：`jobs.py:400-418` 将 PAUSED 视为可删除，不查活动 attempt；`retry:884-907`、`resume:952-985` 等按 ID 读取时不排除 deleted_at；`claim:495-505` 不含 `j.deleted_at IS NULL`；`requeue_recovered_dependencies:1260-1273` 同样没有删除过滤。

**修复**：统一可见任务读取与 mutation guard，已删除 ID 的普通业务操作返回 404/明确已删除错误。所有 scheduler/reconciler 查询必须排除 deleted_at，保留历史证据不等于允许执行。PAUSED 但仍有活动 attempt 或 pending resume 的任务应拒绝删除，或原子撤销恢复意图并等待旧 attempt 结束后再隐藏。旧 attempt 的必要清理仍允许，但不能把 deleted job 变回可运行状态。

**验收**：运行中/暂停未结算/恢复等待任务均不产生隐形执行；删除后 retry/resume/clone/batch 操作的行为一致；依赖恢复扫描不复活已删除任务；历史 artifacts/audit 保留可追溯。

#### SS-04 — P1：项目文件变化后再次导出失败

**步骤**：新项目导出成功；不变重导出返回 reused=true；调用 `ProjectService.import_local_resource(..., kind='LICENSE_EVIDENCE')` 正式导入一个 TXT 许可文件；再次导出。

**实际**：`PROJECT_PACKAGE_OUTPUT_CONFLICT: 同名项目包内容不一致`。用户没有修改包内容、没有触碰数据库，也没有使用路径穿越。

**定位**：`project_packages.py:324-329` 最终包名只取 `_state()` 的 hash；`331-343` 又把整个项目文件树放入包；`347-351` 发现同名 ZIP 内容不同即拒绝。LUT/授权文件等合法 payload 变化不会改变该状态 hash。

**修复**：先冻结 state 和按相对路径排序的 payload 清单，使用 `(schema, state_sha256, [(rel_path,size,sha256),...])` 的规范 hash 作为包身份，或使用明确的版本号并保留完整内容摘要。相同内容仍可复用；任何文件内容增加/变化必须生成新的包名。不要自动覆盖旧包来掩盖摘要冲突。

**验收**：无变化导出复用；新增许可、LUT、普通项目文件、修改包含文件内容分别成功生成新包；旧包保持完整；修改文件 mtime 而内容不变不会错误声称内容变化；并发同一内容导出得到一致包。

#### SS-05 — P1：小说项目包往返遗漏文稿业务记录和项目默认时长

**完整验证链**：创建目标每集 180 秒项目→导入合成 TXT→提交正文第 2—4 段→导出包→调用上传服务层（非浏览器操作）→stage→IMPORT_AS_COPY_REWRITE_IDENTITY。导出 EXPORTED、导入 IMPORTED，数据库外键检查为 0 错误。

**实际差异**：原项目 `source_documents=1`，副本为 0；副本 `latest_for_project=null`；包内仍有原 TXT 文件。项目级 `target_duration_ms=180000` 变成 NULL，但副本单集 target 仍为 180000；导出的 project JSON 根本没有 target_duration_ms 字段。包的 excluded_domains 列表未说明文稿导入状态与时长被排除。

**定位**：`project_packages.py:194-205` 只导出旧项目、分集字段；`291-303` 返回的 state 不包含 source_documents/source_document_versions/import_sessions/import_session_items；`848-871` 导入项目 INSERT 漏掉目标时长。`alembic/versions/0094_project_target_duration.py` 已引入独立项目默认时长，项目包没有同步演进。

**影响**：文件虽然在目录中，用户回到“已导入小说”入口却找不到已确认原稿与正文范围，改编/重新生成缺少业务绑定；随后追加分集或展示默认时长可能回退到产品默认值。不能用 `foreign_key_check=0` 证明项目业务状态完整。

**修复**：扩展项目包 schema/可兼容可选字段，完整携带项目默认 target_duration_ms，以及文稿、版本、已确认正文范围和相应引用。副本导入时统一重写 source document/version/import item IDs 与 media_version_id、episode source lineage；完成后验证原文/text SHA、段落范围、引用归属。运行中的 Job/lease token 不应直接作为可恢复执行状态带入副本；保持既有显式不自动继续生产规则。若某些业务域暂不支持迁移，导出/预检 UI 必须准确列出，不得静默遗漏。

**验收**：导入副本后“最近原稿”、已选正文范围、文本内容、章节/段落数、项目目标时长与原项目一致；新 ID 全部归属副本；没有原项目 choices/worker token；旧版本包导入仍兼容且明确告知缺项。

#### SS-06 — P1：artifact 登记未约束任务归属与 worker 生命周期

**实际**：两个项目分别创建 Job A/B；把文件放到标准 `work/jobs/{job_B_id}/output.txt`；用 A 的 attempt 调用 register_artifact，被登记为 A 的 VERIFIED 产物。A 取消并 complete 为 CANCELLED 后，再登记另一个文件，仍成功 VERIFIED。

**定位**：`jobs.py:1306-1342` 只要求路径在整个 work_root 内、attempt ID 存在；不检查目录属于该 job/attempt、不检查活跃租约与调用 worker，也不检查取消/删除状态。API `ArtifactRegisterRequest` 只有 kind/path，无 lease 身份字段。

**影响**：错误 worker、过时回调或服务调用可能把另一个项目工作产物归入当前任务；取消后仍可以新增已验证产物。此结论是项目数据归属与执行回执缺陷，不等于已证明互联网攻击者能访问服务器。

**修复**：正常 worker 登记接口要求 attempt_id、worker_id、lease_token，校验未取消/未删除和目录归属。输出至少隔离到 `work/jobs/{job_id}/{attempt_id}/...`，避免不同尝试复用路径。已有 provider 对账确实需要在 ORPHANED 状态登记恢复产物，应走单独的恢复入口，验证 provider_job_id、冻结输入、文件清单及用户停止意图，不能简单把所有终态登记一刀切禁止。

**验收**：A attempt 登记 B 路径被拒；错 worker/错 token/过期 token 被拒；取消后迟到普通登记被拒；显式对账恢复仍能在充分证据下成功；跨项目、跨 attempt 的同名文件不互相覆盖。

#### SS-07 — P2：artifact 直接下载不复验完整性，修改后的文件仍标 VERIFIED

**实际**：登记合成文件后替换其字节；artifact_download 正常返回路径，artifact.status 仍 VERIFIED，但记录 SHA 与返回文件 SHA 不同。

**定位**：`jobs.py:1358-1378` 仅验证记录状态和安全路径；同文件 1322 行的登记 hash 不是持续保证。作为对照，`media.py:550-552` 的晋级路径已比较实际 SHA 并拒绝不一致，因此不能声称所有晋级入口也绕过 hash。

**联动证据**：媒体分工独立发现 Comfy 两个不同节点输出同名 result.png 时，复制到同一路径会覆盖；该触发不需要人工篡改文件。详见 `evidence/media_pipeline/comfy_collision_fixture/results.json`。应分别修复上游命名冲突和本处下载完整性边界。

**修复**：产物登记后发布到不可变、含 attempt 或内容摘要的最终路径；文件变化时将完整性状态转为失败并返回稳定错误。下载采用已打开文件/统一完整性服务，避免检查与读取不是同一版本。大视频 Range 请求不宜每段都重新 hash 全文件，可用不可变文件、一次完整验证及身份缓存，但缓存失效规则必须清楚。

**验收**：同名 provider 输出同时保留且 SHA 各自正确；修改/截断已登记文件后直接下载、预览、晋级显示一致错误；完整文件 Range 下载性能保持可用。

#### SS-08 — P2：畸形项目包 JSON 类型使上传接口返回 500

**实际**：仅包含两个小 JSON 文件的合法 ZIP，manifest 为 `[]`（或 state 为 `[]`）即触发 `AttributeError: 'list' object has no attribute 'get'`。通过正式 HTTP 上传接口复验得到 500/Internal Server Error；临时文件确实被清理，未残留 .ldspkg。

**定位**：`project_packages.py:391-393` 未校验顶层对象就 `.get`；424-425 行 except 未覆盖类型错误。`api/routes/project_packages.py:41-53` 只转换 DomainRuleError。

**修复**：用明确结构校验 manifest/state 顶层对象、entries 数组及各元素字段类型，所有用户输入格式错误转换为 PROJECT_PACKAGE_INVALID/STATE_INVALID 的 4xx。限制 metadata JSON 单文件大小、嵌套深度和记录数，再完整性流式校验，避免先 `archive.read` 解压任意大 JSON。不要仅 catch Exception 后继续处理。

**验收**：顶层数组/null/string、条目类型错误、缺关键字段、负数尺寸、重复/越界路径、异常压缩率全部稳定 4xx；无入库、无残留；合法 Unicode 包仍成功。

#### SS-09 — P2：只查看前 128 个候选导致可运行任务被饿死

**构造**：一个 worker 已持有 GPU lease；队列前 128 个高优先级任务都需要该 GPU；第 129 个是普通 CPU 任务；mixed worker 声明 `['CPU','GPU_H3']`。

**实际**：mixed claim 返回 None；马上改用 CPU-only worker 便可领取该 CPU 任务，证明不是依赖/资源本身不可用。

**定位**：`jobs.py:495-505` 在资源过滤前 SQL LIMIT 128，515-524 行才在 Python 中排除活动 resource_key。

**修复**：先过滤资源可用性再 LIMIT；若资源计算暂保留 Python，则对候选分页读取并有明确的公平性保证，不能把“本页都被占用”当作“没有可运行任务”。优先保留单 GPU 串行规则，避免为了吞吐撤销资源互斥。

**验收**：前 128/512 个 GPU 阻塞任务后面的 CPU 任务仍可及时被 mixed worker 领取；保持高优先级/FIFO/依赖顺序；大队列不退化为每秒无界扫描，且 12 worker GPU 互斥正向测试继续通过。

#### SS-10 — P2：outbox 批量投递提前消耗未发送事件的重试预算

**实际流程**：真实 loopback HTTP 接收器固定返回 500，`deliver(limit=2)` 连续执行，用合成时间推进释放过期 claim。第 1 个事件实际 POST 5 次后 DEAD_LETTER 合理；第 2 个事件在此前从未真正 POST，却被预先计数。等它第一次真正 POST 失败时，attempt_count 已为 6，直接 DEAD_LETTER。

**定位**：`outbox_delivery.py:126-144` 批量 claim 时给所有条目 attempt_count+1；159-193 行逐项发送，首条失败后 break，未发送条目没有释放 IN_FLIGHT；回收租约又递增其计数；`213-217` 按这个计数耗尽预算。

**影响**：自动化通知/状态回调因前序事件故障提前失去重试机会；用户可能认为通知已被正常重试 5 次，实际上只尝试了 1 次。

**修复**：将“被领取”和“开始实际 HTTP 尝试”分开，真正发送前原子递增 attempt_count；遇到 break/取消/进程结束时把尚未发送条目恢复可领取状态，保留完整预算。已发送但响应未知的条目继续采用至少一次投递和 event_id 幂等，不能伪造恰好一次。

**验收**：批次第 1 条连续失败，不消耗第 2/3 条的实际发送次数；每条最多 5 次真正 HTTP 尝试后进死信；同 endpoint 并发 dispatch 不重复 claim；恢复、redirect 阻断、成功去重继续通过。

### 4. 安全边界观察与未验证事项

#### R-01 — 带配置前提的 Host 边界风险，不能描述成已成功浏览器攻击

显式配置 `network_mode=LAN_SERVICE`、`host=0.0.0.0`、`trusted_lan_unauthenticated=true`，用模拟 LAN peer 请求未配置的 `http://unconfigured-audit.invalid:3210`：GET health 返回 200 和实例 token；无 token 写请求被 403 阻断；提供 token 且 Origin 与该任意 Host 相同，合成任务 cancel 成功 200。

根因位于 `middleware.py:53-78` 的 Origin==Host 兜底和读取响应发 token（357-363 行附近），没有对 Host 做独立许可检查。代码显式支持“可信 LAN 无认证”，所以不能把 LAN 用户正常访问本身报为漏洞。本测试说明该模式缺少 DNS rebinding 所需的一层 Host 防线；是否能在实际 Windows/Chrome 通过 local network access 限制完成浏览器利用，未执行验证。

建议提供明确允许 Host/绑定地址的校验，任意公共域名 Host 不自动获得信任，同时保留配置过的 LAN IP/域名。此项优先级应结合实际是否启用 LAN 决定。

#### 其他审查结论及限制

- 受控路径测试实际拒绝 `..`、绝对路径、Windows 驱动路径、symlink 与 ZIP 保留名称；没有发现本次输入能读出任意本机文件。
- 代码中的常见 subprocess 调用使用参数列表；检查范围内未发现 `shell=True`、os.system、eval/exec 或 pickle 反序列化入口。该结论不等于每个 FFmpeg 参数组合均已验证，媒体分工负责特殊路径和渲染实测。
- ProviderConnection 的公开返回/审计/数据库使用模拟 SecretStore 检查通过；真实 Windows Credential Manager 未测。显式 reveal-secret 功能本来就会返回密钥，不能把授权“显示密钥”操作本身当泄漏。
- 项目包 `archive.read` 的 JSON metadata 没有独立小额限额，展开上限与 ZIP ratio 很宽；仅将其记作 SS-08 修复时应补齐的资源边界，没有实际创建超大压缩包或耗尽磁盘。
- `ProjectPackageService._state()` 多表读取未显式开启一致性 read transaction；导出与并发业务写入的点时一致性需要专项验证。本次正常往返验证未证明并发快照一致，因此不宣称其通过。
- SQLite 在线备份、真实事务强杀与 lease 强杀测试通过；NTFS 杀进程/断电、磁盘耗尽、多实例更新、真实子进程模型取消仍属于剩余环境项。

### 5. 建议按阶段交付给修复 AI

#### Phase 1：先修停止/恢复/删除语义（SS-01—SS-03）

只涉及 jobs、worker_sessions、必要 API 契约和现有测试。建立共享事务内状态转换，不增加并行的第二套状态机。以本报告 6 个故障场景全部修复、4 个原正向状态场景和真实 SIGKILL 场景仍通过作为出口。

#### Phase 2：保证产物归属与不可变性（SS-06、SS-07，与媒体同名冲突联修）

先修输出目录身份，再给普通登记增加 worker lease 校验和受控恢复入口，最后统一下载完整性语义。保留 UI 对损坏/不可用产物的可读提示；不自动把坏文件重复标成 VERIFIED。

#### Phase 3：保证项目包可完整往返（SS-04、SS-05、SS-08）

扩展版本契约和字段映射，修正内容寻址，再补错误输入边界。以包含原稿、正文范围、目标时长、LUT、许可文件、媒体、资产状态的项目进行导出/副本导入/再导出比对；对排除的运行状态准确声明。

#### Phase 4：调度吞吐和自动化可靠性（SS-09、SS-10）

修候选页资源过滤和实际 HTTP 尝试计数；保留单 GPU 资源互斥及事务性 claim。以 128/512 阻塞队列、并发 claimant 和多事件前项失败作为验收场景。

#### Phase 5：平台环境回归

根据实际使用模式决定 LAN Host 防线优先级。在 Windows、真实 3090 Ti、真实本地模型上补充运行中取消/关闭/崩溃恢复、中文和单引号路径、长视频产物、磁盘空间不足、恢复旧备份等测试。此阶段不应以 Linux fake provider 通过替代。

### 6. 可复跑材料

脚本位于 `harness/state_security/`：

- `reproduce_state.py`：第一批 10 场景，自动创建迁移后的种子库。
- `reproduce_boundaries.py`：第二批 9 场景，复用第一批种子库。
- `reproduce_packages_and_crash.py`：小说包往返、HTTP 错误包、两个真实 SIGKILL 场景。
- `verify_controls.py`：单 GPU 并发 lease、模拟密钥脱敏、上传限额。

在工作目录依次使用上述 Python 运行四个脚本；结果输出到 `evidence/state_security/` 下四个 JSON，具体数据目录包含运行时间。脚本不修改 repo 生产文件，全部使用合成资料。复跑前应按本机位置调整解释器和 BASE/REPO，保持所测代码提交固定。

交付可仅打包 4 个脚本、4 个结果 JSON、日志与 source_evidence.json；111MB 左右的合成 SQLite/文件夹是可重建中间证据，不必全部打入最终用户文档附件。


## 附录E：媒体生成合同、真实后期与交付

### 1. 本轮结论与验证边界

对仓库提交 **`8a63c604a1a13556dbe277d312ecf73bebb52883`** 的媒体生产、后期和交付链路进行了独立测试。11 个关键源码文件按 Git blob SHA 校验，与取得的当前提交完全一致；证据为 `evidence/media_pipeline/source_integrity.json`。仓库工作树中未检出 `AGENTS.md`，本分任务未修改产品源码。

**已完成一条真实 CPU 媒体闭环：合成测试片段及本地 Flite 测试语音 → 导入和机器 QC → 显式测试审核 → 对白候选登记和采用 → BGM、SRT、v3 TimelineRevision → 持久 Job/CPU Worker → 整集渲染 → 未审核交付拒绝 → 测试审核 → 交付文件校验 → HTTP 下载 → OTIO/EDL/剪映草稿结构导出。** 输出为 **8 秒、320×180、24 fps、192 帧、H.264 + AAC**，同时烧录字幕并生成 SRT。另跑通缩略图、filmstrip、waveform、预览 proxy、首/末/指定时间帧提取、CPU 320×180→640×360 后处理及 FFmpeg 取消/超时。

这条闭环的画面是 FFmpeg 测试图/色块，语音是本机 FFmpeg/Flite 生成的英文测试语音。它验证真实媒体文件、真实 FFmpeg、真实数据库、真实服务/API 和持久任务链路，**不代表已用用户小说生成真实短剧，也不代表 Qwen、H3、VoxCPM2、LatentSync、Real-ESRGAN 的真实推理通过**。测试审核记录明确标记 TEST FIXTURE，不作模型画质、角色一致性、配音自然度或商业可用性的结论。

已实际查看成片 1 秒、5 秒的抽帧，英文字幕位于画面底部且未被裁出画框，证据保存在 `reviewable_media/frame_1s.png`、`frame_5s.png`。这个抽查只证明这两帧的字幕存在和基本放置，不代表完整字幕排版、中文字体或人工视听验收。脚本中的审核评论是程序提交的测试决策，不是审核 UI 的人工操作记录。

本分报告确认 **9 项问题：P1 4 项、P2 5 项**。另有真实 GPU/Windows 环境下必须补测的事项，不计入“已实证代码缺陷”。所有问题均能追溯至本轮新建脚本、实际输出或当前代码，不采用旧报告的结论充当新结果。

### 2. 完整闭环的可复核结果

主证据：`evidence/media_pipeline/happy_path_complete/results.json`，`success=true`，12 个阶段全部完成。复现脚本：`run_delivery_happy_path.py`。

| 环节 | 本轮实际结果 | 意义及限制 |
|---|---|---|
| 导入 | 两段各 4 秒视频、一份 4 秒 Flite 测试语音、一份 8 秒 BGM，写入隔离项目和 MediaVersion | 输入真实存在，均为测试夹具 |
| 机器检查 | 两份视频和语音均 PASS，随后写入显式 TEST FIXTURE 审核 | 语音走实际 LUFS、True Peak、峰值/削波/静音检查 |
| 对白 | 创建对白和本地音色；测试音频登记 `IMPORTED_LOCAL_AUDIO` / `PREVIEW` 候选并采用 | 不把导入候选伪称 VoxCPM 正式生成 |
| 字幕 | 从已导入脚本文本建立 SRT，冻结 subtitle revision | 英文测试字幕在 1 秒、5 秒截图中可见；未验证本机缺失的中文字体 |
| 时间线 | v3 revision，两个 VIDEO、一个 DIALOGUE、一个 BGM，第二镜头 DISSOLVE | 混音参数冻结在 revision；没有替换为旧式动态音轨读取 |
| 持久任务 | `/compose:submit` 返回 202，`LocalMediaWorker.run_once` 实际执行后 Job / Attempt 均 SUCCEEDED | 并非直接写成功状态 |
| 整集渲染 | `VERIFIED`；8,000 ms；320×180；24/1 fps；192 帧；AAC；`subtitle_burned_in=true` | `ffprobe.json` 与真实文件对应 |
| 交付门禁 | 未批准 render 的交付请求返回 422，`EPISODE_RENDER_APPROVAL_REQUIRED` | 已检查拒绝路径 |
| 交付生成 | 对测试 render 显式批准后，生成 MP4、SRT、manifest | 交付包自己的 human/platform review 仍是 PENDING，没有自动变为发布批准 |
| 文件校验 | MP4、SRT、manifest 的实际 SHA-256/大小及交付字幕合同全部通过 | 不只检查文件存在 |
| 下载 | HTTP 200 下载 538,298 字节 MP4，hash 与交付登记完全相同 | 产品下载端点返回单个 MP4；不是整个 ZIP |
| 断点读取 | render `Range: bytes=0-1023` 返回 206，1,024 字节，正确 Content-Range | 可定位媒体播放/拖动所需的范围读取 |
| 编辑器导出 | OTIO+EDL、剪映草稿生成成功 | 仅验证文件结构和媒体复制；未在第三方桌面编辑器打开，且见 MED-09 保真问题 |

成片 SHA-256：`f8fb792bf5e301ebd06f08bb17034a161f9ccd91f8f656a13b87274511744566`。

便于主报告打包的目录：**`evidence/media_pipeline/reviewable_media/`**，包含 MP4、SRT、产品原始 manifest、ffprobe、1 秒/5 秒截图及本地 hash 清单。`happy_path_complete/audit-copy-of-local-delivery-package.zip` 是审计脚本为便于复核打包的原始本地交付目录，**不是产品提供 ZIP 下载功能的证据**。

### 3. 媒体功能覆盖矩阵

“仓库测试映射”表示已找到相关入口和现有测试，具体运行数量及失败归因由后端全量测试分报告统一统计；不能将此类条目全部算成本分任务的运行通过。真实模型推理与普通控制逻辑分别记载。

| 功能 | 本分任务的验证方式 | 结果/覆盖边界 |
|---|---|---|
| 角色、场景、道具、服装图片 | 入口和 `test_asset_image_idempotency`、`test_scene_prop_asset_generation`、关键帧测试映射；实际图片导入/派生链 | 真实 T2I 推理阻塞；图片文件链可测 |
| 多视图/表情/Identity Pack | `test_t2i_probe`、`test_generation_variants` 中多视图与身份包引用/过期测试映射 | 未用模型实测身份一致性、九宫格质量 |
| Comfy workflow 编译和语义绑定 | 当前 `workflows.py` / `comfy_jobs.py` 追踪；现有测试映射；独立本地 HTTP 输出收集试验 | 确认 MED-01；未启动真实 ComfyUI |
| Comfy 提交/轮询/取消 | 源码检查 provider acceptance unknown、history、queue ownership、interrupt 分支；现有测试映射 | 有避免盲重提和误中断其他任务的设计；真实提供方长任务另测 |
| T2V/I2V/首尾帧/Ref2V/运动控制 | `generation_variants` / H3 factory / geometry 与 preflight 测试映射 | 不声称 H3 权重加载、动作控制或首尾帧画面效果通过 |
| 帧提取 | 对真实 4 秒 24 fps 视频提首帧、末帧、1.234567 秒帧，并提交 8 秒越界请求 | 首帧 0/0；末帧 95/3,958,333µs；指定点解析为第 29 帧/1,208,333µs；越界明确拒绝 |
| 镜头连续性 | 当前约束检查与测试映射；提取真实帧证据 | 未用真实 AI 输出评估跨镜头面容/服装漂移 |
| 普通视频拼接/时长 | 正常 24 fps 两片段、混合 24/30 fps CUT、实际整集 8 秒 | 正常合成通过；前置空白语义存在 MED-05 |
| FADE/DISSOLVE | 同 24 fps DISSOLVE、混合 24/30 fps DISSOLVE 对照 | 同帧率通过；混帧率稳定失败，MED-03 |
| 分段合成 | `test_segmented_render`、duration guard 测试映射 | 不把低层普通拼接等同于 H3 长片段续写质量 |
| 本地 Windows SAPI | 当前 handler 与 `test_local_sapi_tts` 映射 | 当前 Linux 无 Windows SAPI，实机功能阻塞 |
| VoxCPM2 参数 | 同文本/音色、不同语速/情绪的运行时调用记录器；实际后处理 FFmpeg | 参数丢失 MED-04；无 VoxCPM 推理 |
| 参考音色/克隆 | voice profile/候选/授权证据代码与现有测试映射 | 未克隆真实用户音色；没有权重/参考音频授权素材 |
| LatentSync | submit/handler/runtime 输入媒体 hash/项目检查与输出 probe 代码审核；现有测试映射 | GPU/权重缺失；嘴型同步效果与进程取消不能宣布通过 |
| 对白候选、采用、文本权威 | 完整闭环实际建对白、导入试听候选、采用、字幕来源绑定 | 通路通过；候选为 IMPORTED_LOCAL_AUDIO/PREVIEW |
| BGM/SFX 音轨 | 实际创建绑定、增益/循环/淡入淡出；波形取样验证 end_us | 完整合成通过；非循环范围越界 MED-02 |
| 音频 QC | 实际检查测试语音响度/峰值/削波/静音 | 测试语音 PASS；不推断语音可懂度与情绪正确 |
| SRT/字幕烧录 | 脚本权威 SRT，真 FFmpeg 烧录，对照普通/单引号/中括号目录 | 正常通过；单引号目录失败 MED-06 |
| ASS/VTT/样式模板 | 当前输出实现和仓库测试映射 | 本轮主闭环使用 SRT；中文 Windows 字体与逐 cue 样式须实机补测 |
| 缩略图 | 实际生成 WebP | 220 字节测试缩略图；输入为低复杂度色块 |
| filmstrip | 实际 FFmpeg 生成 WebP | 800 字节测试条带 |
| waveform | 实际音频生成 PNG | 470 字节测试波形 |
| 视频 proxy | 实际生成 H.264 预览 MP4 | 4,042 字节测试 proxy；不以此小尺寸测量真实剧集吞吐 |
| CPU 后处理/放大 | 实际发布 recipe，SCALE→TECHNICAL_QC→ENCODE | 320×180→640×360、4 秒/24 fps、QC PASS；不称为神经超分辨率 |
| Real-ESRGAN NCNN/Vulkan | 当前 executor 审核、批次/进程树/租约/完整性等现有测试映射 | 真实 Vulkan GPU、模型和外部 exe 未具备 |
| FFmpeg 取消/超时 | 对真实实时限速 FFmpeg 子进程触发取消和 1 秒 timeout | 分别 `JOB_CANCELLED` / `FFMPEG_EXECUTION_TIMEOUT`；耗时约 1.753/2.792 秒 |
| 失败/重试文件清理 | 真 FFmpeg 烧字幕失败后检查目录 | 遗留完整中间合成文件，MED-07 |
| 整集审核/交付门禁 | 真实 API 先拒绝再测试批准，文件 SHA-256 verify | 通过；独立交付人工/平台审核仍 PENDING |
| 导出/下载 | 实际 MP4下载/Range及 OTIO/EDL/剪映结构导出 | MED-08、MED-09；第三方桌面兼容性未跑 |

### 4. 确认问题及具体修复方案

#### MED-01 · P1 · Comfy 多输出同名文件相互覆盖，任务仍成功、artifact 仍 VERIFIED

**触发和复现：** 提供方历史中两个节点分别输出 `node-a/result.png`（红图）、`node-b/result.png`（蓝图）。`probe_comfy_output_collision.py` 用本机 HTTP 服务模拟提供方的 history JSON，传输、PNG 文件、SQLite 和产品 `ComfyGenerationService.poll_attempt` 均实际执行。由于不含真实 Comfy/GPU，此测试只证明产物收集/登记问题。

实际结果为 Job `SUCCEEDED`，返回两条 artifact，**两条使用相同 ID 和同一路径**。登记 SHA 是红图 `fc78d71a…`，路径中的实际 SHA 是蓝图 `ea6d8e2f…`；两条仍为 `VERIFIED`。证据：`comfy_collision_fixture/results.json`，`distinct_paths=1`、`all_verified_artifacts_match=false`。

**根因：** `apps/api/local_drama/application/comfy_jobs.py:803-812` 将来源压平成 `output_root / source.name`；job 级而非 attempt 级目录进一步放大重试覆盖风险。`register_artifact` 对相同 attempt/kind/path 的重入不会生成独立产物。不可把下载/晋级的其他 hash 门禁说成必然可绕过；这里已直接证明的是收集时覆盖和成功状态失真。

**实现方案：** 定义输出身份为 `(job_id, attempt_id, node_id, output_slot, ordinal, source_relative_path)`，落入独立 attempt 目录。目标名至少带稳定输出索引/原相对路径 hash，禁止仅 basename。先将所有输出写入临时目录并分别验证大小/hash/媒体类型，再登记数据库和公布成功。相同身份重放须验证内容相同；不同内容应 quarantine/NEEDS_ATTENTION，不覆盖。为历史出现同一路径或 hash 错配的产物提供扫描清单和重新采集命令，保留原审计记录。

**验收：** 两节点同名、大小写仅不同、相同 basename 不同子目录、多输出重放、第二次 Attempt 同名输出均保留独立 artifact；每个 VERIFIED 文件重新计算 hash 均相符；后置处理失败不能将整条任务显示为完整成功。

#### MED-02 · P1 · 非循环 BGM/SFX/对白未按 end_us 截断，会串到后续镜头

**复现：** 输入 3 秒 440Hz 音频，指定 BGM 范围 0.5–1.5 秒，`loop_enabled=false`，整集长度 4 秒。真实公开 `create_track → create_timeline_revision → render_episode` 返回 `VERIFIED`，但 2.0–2.5 秒 RMS 为 **4204.19**，仍有声音。相同参数 `loop_enabled=true` 的低层对照，在范围后 RMS 为 0。证据：`public_services/results.json`、`boundaries/results.json`。

**根因：** `timeline.py:3267-3291` 只在循环分支追加 `atrim=duration=...`；非循环仅 adelay，完整源音频继续进入混音。提交层允许短于源素材的绑定范围，因此不是不可达的私有方法输入。

**实现方案：** 将所有绑定统一编译为 source trim → `asetpts=PTS-STARTPTS` → loudness/gain/fade → delay → target trim。是否循环只决定源是否 repeat，不能决定是否遵守结束点。持续使用整数 µs/有理数时间；避免目前三位小数裁切引入不必要误差。对白如不允许静默截断，提交前明确阻止超长对白并提示缩短台词或调整语速；不得把错误范围先接受后在混音中忽略。

**验收：** 对 BGM/SFX/DIALOGUE、loop 开关、非零 start、短于/等于源长度和淡出组合进行实际 WAV/MP4 取样；end_us 后不再有该音轨分量，且下一镜头对白不会被前一条配音覆盖。低层修复后提升 renderer contract，避免复用历史错误成片。

#### MED-03 · P1 · 24/30 fps 混合视频转场必现失败，预检却允许执行

**复现：** 两段真实 MP4，均 160×90/2 秒，分别 24 和 30 fps，第二段选 DISSOLVE。公开时间线和 preflight 接受；FFmpeg 报：`xfade timebase (1/12288) ... second ... (1/15360)`，`FFMPEG_EXECUTION_FAILED`。同为 24 fps 的 DISSOLVE 成功，混合帧率 CUT 也能产生文件。证据：`boundaries/results.json`、`public_services/mixed_fps_preflight.json` 和 `public_services/results.json`。

**根因：** `timeline.py:2828-2946` 单片归一化统一尺寸/SAR/像素格式，但没有显式统一输出 fps 和 timebase；`2947-3030` 直接串 xfade。生产规格的后续放大无法修复转场阶段已经失败的问题。

**实现方案：** 在不可变渲染计划中冻结工作 fps（优先项目/交付分数 fps）和时间基；每段明确 `fps=num/den,settb=AVTB,setpts=PTS-STARTPTS`，统一像素格式、尺寸和 SAR，再合成。所有转场 offset 用累计帧数换算，避免多段浮点误差。preflight 应报告需重采样的素材与处理策略，不能检查时长后就默认可转场。

**验收：** 24/25/30/30000÷1001、不同 timebase 的相同 fps、可变帧率输入、CUT/FADE/DISSOLVE 都验证；输出帧数/时长符合冻结的规格。连续十段转场无漂移；不同输入的音频不造成 A/V 不同步。

#### MED-04 · P1 · VoxCPM2 语速/情绪只进元数据，未进入实际生成或后处理

**复现性质：** 运行时参数转发契约测试，不是模型试听。对同一文本与音色分别提交 `neutral/0.5×`、`angry/2.0×`；用调用记录器接收产品发给 VoxCPM 的参数，并输出相同测试 WAV，再由产品运行真实 FFmpeg。两次运行时调用参数完全相同，后处理输出 SHA 也相同。证据：`public_services/results.json` 中 `voxcpm_emotion_and_speed_forwarding`。

**根因和影响：** `dialogue.py:587-645` 校验并冻结 emotion/speech_rate；`worker_handlers/tts_job.py:139-180` 的 Vox 分支只传 text/prompt_audio/prompt_text；`infrastructure/local_ai_subprocess.py:95-109` 同样没有速率/情绪参数。SAPI 只消费语速，情绪也未被消费。UI `DirectorSoundInspector.tsx:182-188,223-224` 提供 0.75–2× 下拉，并在对白超长时要求用户调整语速重生配音；VoxCPM 路径下该修复建议可能无法改变超时。

**实现方案：** 给 provider 增加显式参数能力声明，并冻结 `requested_parameters` 与 `applied_parameters`。如果模型无可用原生语速控制，使用有明确标记的 FFmpeg `atempo` 后处理实现速度，随后重新测量时长、生成字词对齐、使旧字幕/口型产物过期。情绪若受支持，编译成正式模型输入并记录；若不支持，UI禁用并解释，服务拒绝非默认值。不要继续接受、显示已设置、实际上忽略。SAPI 也需按能力处理情绪，避免假承诺。

**验收：** 同文本同 seed 的 1×/1.5× 输出长度与 applied_parameters 相符；不可用参数在提交时明确提示；运行时调用捕获测试证明参数实际传递；真实模型试听另外验证音质、音高和情绪效果。

#### MED-05 · P2 · 时间线起点不为 0 时，渲染与导出使用不同时间原点

**复现：** 唯一 VIDEO item 位于 1–3 秒。公开创建、preflight、render 都成功，render 时长 **2 秒**，且状态 VERIFIED；同一个 revision 的 OTIO 包含 `Gap.1=1秒`、`Clip.2=2秒`，总长 **3 秒**。证据：`cleanup_gap/results.json`，并非两个不同时间线之间的比较。

**根因：** `timeline.py:2734-2761` 使用 `max(end)-min(start)` 作为整集时长，`_concat_timeline_videos` 从输出0秒连续拼接；音轨/字幕仍基于原绝对 start_us。与此同时 `timeline_exports.py:141-155` 显式生成前置 Gap。

**实现方案：** 先决定产品时间线是否支持空白。最小实现是：若编辑器只支持连续镜头，创建/编辑/preflight一致拒绝前置或中间 gap、未经定义的 overlap，并给可定位错误；若支持，建立统一0秒原点，为空白插黑画面/静音，各轨与字幕同一时间轴。不要在 renderer 隐式平移视频而保留其他轨道绝对时间。输出、播放器和各导出复用同一份编译计划。

**验收：** 首段从1秒开始、中间空1秒、尾段结束3秒、多音轨/字幕偏移组合，在预览、render、OTIO中一致；不支持的布局在提交前拒绝，不能静默裁切后标 VERIFIED。

#### MED-06 · P2 · 工作目录包含单引号时，烧字幕失败

**复现：** 同一视频和 SRT，`normal path`、`studio[demo]` 目录成功；`creator's studio` 失败，FFmpeg 尝试读取去掉单引号后的 `creators studio/...srt`。真实错误与命令保存在 `boundaries/results.json`。用户 Windows 用户名或项目父目录包含单引号时存在同类滤镜解析风险；当前实证平台是 Linux FFmpeg 6.1.1。

**根因：** `timeline.py:3044-3068` 对路径单层 replace 后再拼 `subtitles=filename='...'`，没有正确跨越 FFmpeg 选项和 filtergraph 的多层转义。

**实现方案：** 将滤镜执行工作目录设置为受控渲染临时目录，使用只含安全字符的相对字幕文件名，视频和目标路径继续通过 argv 传入；或集中实现经过实际 FFmpeg 验证的多层 filtergraph 路径编码器。不要使用 shell=true。同步覆盖字幕、drawtext/fontfile、lut 等路径型滤镜参数。

**验收：** 空格、中文、单引号、冒号（Windows盘符）、逗号、中括号、反斜杠目录均用真实 FFmpeg 烧录；截图需确有字幕，不能仅以退出码0验收。

#### MED-07 · P2 · 合成/字幕/放大中途失败时，中间完整视频遗留

**复现：** `_concat_and_mix` 成功产生拼接视频后，利用 MED-06 的真实字幕失败结束；目录留下 `.partial-....mp4`，本次4,821字节。证据：`cleanup_gap/results.json`。这个小文件来自测试片，长剧同一分支会遗留更大的拼接中间文件；本轮未跑长片做磁盘压测。

**根因：** `timeline.py:3104-3136` 的 concat、字幕烧录、放大步骤发生在清理用 `try/finally` 之前；`3143-3149` 的 finally 只能覆盖后续混音/mux。若早期步骤抛错，清理无法进入。

**实现方案：** 最外层使用 per-attempt 临时目录/ExitStack，将所有创建路径立即登记并确保所有阶段都在 finally 内；最终输出仅通过 QC后原子移入正式目录。另提供仅清理无活跃租约、无引用、超过保留期的 orphan-partial 命令，避免误删可恢复任务。

**验收：** 在拼接、字幕、放大、混音、mux分别注入真实失败/取消，均无未登记大文件遗留；成功产物和其他正在执行的 Attempt 文件不受影响。

#### MED-08 · P2 · BOTH/SIDECAR 已生成字幕文件，但交付页只提供 MP4 下载

**证据：** 完整闭环实际产生 MP4/SRT/manifest并校验通过；`/delivery-packages/{id}/download` 只返回 MP4，`timeline.py:3880-3893` SQL只取 `%.mp4`。`api/routes/timeline.py:658-679` 的 files接口只列元数据。`DeliveryWorkflowPanel.tsx:222-223` 的独立字幕/manifest只显示路径文本，唯一下载链接明确为“下载 MP4”。这不是“ZIP下载实现错误”，而是字幕/manifest交付渠道的功能缺口。

**影响：** 用户已选择外置字幕，交付显示完整，但从交付页面无法直接拿到外置字幕及完整包；在远程浏览器场景中，服务器相对路径文本本身不能完成文件交接。

**实现方案：** 保留现有 MP4链接；新增“下载交付包 ZIP”与各已登记文件的下载链接。ZIP只由 delivery_files/manifest的已验证清单构建，服务端解析file_id，不接受任意客户端路径；下载前重新校验文件hash/大小或使用已有受控不可变快照。空字幕模式只打包已实际生成文件。页面无需扩展复杂新工作流，一个整包按钮和每行小下载动作足够。

**验收：** NONE/BURN_IN/SIDECAR/BOTH 的文件集合一致；远程浏览器可取得 .srt/.ass/.vtt、MP4、manifest；缺失/篡改文件不会给出“成功下载完整交付包”；ZIP解压可核验manifest。

#### MED-09 · P2 · OTIO/EDL 静默丢失转场和混音参数

**复现：** 完整闭环时间线第二段为 DISSOLVE，BGM gain=-24dB、淡入淡出各250ms；实际成片确实使用这些参数。导出的 OTIO所有 track/clip `effects=[]`，clip metadata仅有timeline_item_id；EDL事件均为 `C`，没有转场信息；导出返回结果没有 loss/warning 字段。证据：`export_fidelity.json` 和实际 .otio/.edl。

**根因：** `timeline_exports.py:141-209` 仅导出媒体与range；`210-231` EDL固定写CUT（固定 `C` 位于222行）。`parameters`虽然已读取，但这些效果没有进入可重建的表达，也没有作为元数据或降级说明保留。

**实现方案：** 第一阶段先把导出定义为“基础剪辑交换”，增加 `losses[]`/`unsupported_features[]`，列出每个受影响item、丢失的转场/增益/淡入淡出/响度处理，并在UI下载前显示短说明；原始参数写入export manifest，方便人工恢复。第二阶段按格式正式支持的表达编码可承载效果；无法准确表达的流程可提供“烘焙后的音频stem/效果片段”选项。不要默认将“文件生成成功”展示为“编辑效果完整保留”。

**验收：** 导出解析器/目标编辑器回读后核对片段时间、转场、BGM相对音量；所有不能保留的效果都有具体警告及定位。第三方编辑器版本兼容需在真实桌面打开验收，不能由本轮JSON结构检查代替。

### 5. 已验证的取消能力与尚未证实的风险

#### 5.1 与后端报告交叉确认：BKT-08 首次启动漏初始化审核模板

本项由后端报告统一编号 **BKT-08**，不另加 MED 编号，也不重复计入本分报告的 9 项问题。`main.py:126-136` 把模型清单同步和审核模板初始化放在同一 `try` 内，先执行 `ProfileService.sync_manifest`，再执行 `ReviewService.ensure_templates`。缺少清单时异常被捕获、API继续启动，但不依赖模型的内置审核模板被一起跳过。

本分任务补做了实际 API/SQLite 对照：全新迁移数据库、明确不存在的清单路径，使用真实 FFmpeg 产生 PNG 后正常导入关键帧；API启动后模板数量 **0**、`review-targets` 返回 **200/total=0**、后期 overview `pending_count=0`、下一操作为 `OPEN_POST_EDIT`。不添加模型清单、不修改产品源码，仅在隔离对照中调用产品已有 `ensure_templates`，模板变为 **6**、同一API目标数和待审数变为 **1**、下一操作变为 `OPEN_REVIEW`。证据：`review_template_startup/results.json`，`confirmed=true`；可复现脚本 `probe_review_template_startup.py`。

修复应将独立内置数据初始化与可选模型同步拆开，按依赖分别处理异常；清单不可用时仍能审核导入素材、使用CPU后期。对已有空模板库提供幂等补初始化，并让健康状态明确区分“审核初始化失败”和“AI模型未配置”。验收覆盖清单缺失/无效/正常、新旧数据库、重复启动，审核模板和现有待审素材不能随模型可用性消失。上文媒体闭环在脚本中显式调用过 `ensure_templates`，因此其成功不否定本项首次启动缺陷。

#### 5.2 取消与超时

`probe_ffmpeg_cancellation.py` 启动真实实时限速FFmpeg，0.5秒后使cancel_check为真，实际约1.753秒返回 `JOB_CANCELLED`；另设timeout=1，约2.792秒返回 `FFMPEG_EXECUTION_TIMEOUT`。因此不能笼统写“项目取消功能没实现”。

`LocalAiSubprocessRuntime.run_task`（`infrastructure/local_ai_subprocess.py:63-80`）与 `run_lipsync`（`:174-183`）仍使用阻塞 `subprocess.run`，分别可等1,800/3,600秒，签名不提供取消回调。对其真实GPU运行时是否会被宿主/其他机制及时终止，本轮未用实际模型验证。应列为**必须补测的风险**，不计入9项确认问题。与之不同，NCNN执行器已存在明确的cancel_check及owned process tree回收实现，不应把所有模型执行器一概归为同一种问题。

### 6. 下一轮实现顺序与验收门槛

| 阶段 | 任务 | 完成标志 |
|---|---|---|
| Media Phase 0 | MED-01 输出身份/attempt隔离/原子登记；MED-02 全轨end_us；MED-04实际参数或明确禁用 | 不再出现VERIFIED但内容错配、音轨越界、界面参数空转；将诊断脚本的坏行为断言改成修复后的回归预期并通过 |
| Media Phase 1 | MED-03统一fps/timebase；MED-05统一时间原点；MED-06滤镜路径；MED-07全阶段清理 | 连续片段/混帧率/有空白的支持合同一致；特殊目录可烧字幕；失败无未登记中间大文件 |
| Media Phase 2 | MED-08整包与单文件下载；MED-09导出损失声明及可支持效果编码 | 远程浏览器交付完整；编辑器交换能明确知道哪些效果保留/丢失 |
| Media Phase 3 | 仓库目标硬件（如 RTX 3090 Ti）或用户实际 Windows/GPU 机器执行真实模型验收 | 以下硬件矩阵有真实Job/模型hash/输出/耗时/显存证据，不允许使用本报告夹具顶替 |

真实模型验收至少覆盖：角色/场景/道具/服装各一轮文生图；同角色三视角和跨镜头身份；T2V/I2V/首尾帧/Ref2V/运动控制；不同长宽比/尺寸/fps；真实TTS中文/标点/多音字/短句/长句/语速；授权音色2–15秒参考克隆；ASR/ForcedAligner与字幕声画对齐；LatentSync的音视频长短不一及无脸/多脸；30秒以上分段和最终2分钟剧集；目标GPU显存边界/模型切换/长任务取消/断电恢复；Real-ESRGAN真推理；Windows中文/特殊字符目录和字体；最终交付及目标剪辑软件打开。

### 7. 复现脚本与证据清单

所有脚本通过 `--repo` 读取当前代码，写入独立证据目录。完整重跑时使用新的输出目录，避免本轮已存在项目code/幂等键影响结果。当前解释器为 `/workspace/scratch/0102188ff063/audit_quality_venv/bin/python`；此venv是测试工具环境，不代表用户发行包已经包含所有依赖。

| 脚本 | 输出 | 内容 |
|---|---|---|
| `probe_media_boundaries.py` | `boundaries/results.json` | 9组实际FFmpeg边界/对照 |
| `probe_public_media.py` | `public_services/results.json` | 5组公开服务补测，fresh SQLite；含真实派生文件与明确标记的Vox参数记录器 |
| `probe_comfy_output_collision.py` | `comfy_collision_fixture/results.json` | 本地HTTP+真实PNG+SQLite复现同名覆盖；无真实提供方推理 |
| `run_delivery_happy_path.py` | `happy_path_complete/results.json` | 12阶段完整媒体交付闭环，success=true |
| `probe_cleanup_and_gap_export.py` | `cleanup_gap/results.json` | 2秒render/3秒OTIO，以及失败残留文件 |
| `probe_ffmpeg_cancellation.py` | `process_controls/results.json` | 真实FFmpeg取消与超时2组 |
| `probe_frames_and_cpu_enhancement.py` | `frames_enhancement/results.json` | 首/末/指定帧、越界、实际CPU后处理 |
| `probe_review_template_startup.py` | `review_template_startup/results.json` | BKT-08跨模块复核：缺模型清单时模板0/待审0；隔离初始化后模板6/待审1 |
| `source_integrity.json` | — | 11个关键源码blob与当前提交一致 |
| `export_fidelity.json` | — | 完整闭环生成的OTIO/EDL中效果丢失证据 |
| `reviewable_media/` | — | 可直接核验的8秒MP4、SRT、manifest、ffprobe、截图和说明 |

`synthetic_model_manifest.json` 只供后端在**独立fixture副本**重放控制逻辑：明确没有模型权重、GPU、可执行插件或成功推理，路径故意不存在，Comfy监听=false，候选为BLOCKED。原始从0基线不得填入该文件。另有两个早期happy-path输出目录保留了审计脚本自身的路由/下载类型假设纠正记录；最终验收只采用 `happy_path_complete`，不把这两次脚本错误列为产品BUG。


## 附录F：前端生产构建、全量组件测试与代理集成

### 1. 版本与结论

本报告针对当前提交 `8a63c604a1a13556dbe277d312ecf73bebb52883`。测试前对 `apps/web` 的 **713个源文件逐个计算Git blob SHA并与GitHub当前树比较，全部一致**。没有修改生产代码，未使用旧版本结果替代当前测试。

已完成生产构建、现有全量前端组件测试、独立缺陷复现、真实Vite HTTP启动、真实Vite→API二进制小说导入集成，以及页面/布局源码审查。**真实浏览器导航被平台安全检查链阻止，因此没有执行真实页面点击、截图视觉审核或窄窗口实测；组件DOM交互与HTTP接口测试不能代替这些项目。**

| 检查项 | 结果 | 证据 |
|---|---|---|
| 当前前端源文件完整性 | 713/713 SHA匹配 | `evidence/ui_audit/frontend_source_integrity.json` |
| TypeScript类型检查 | PASS | `evidence/ui_audit/build-final.log` |
| Vite生产构建 | PASS；502模块 | 同上 |
| Bundle预算检查 | PASS；54 chunks，最大295.2KiB | 同上 |
| 原始Vitest配置 | 152文件；641通过、7失败；8个未处理异常 | `vitest-final.log/json` |
| Node24适配后的全量Vitest | **152文件、648/648通过，0未处理异常** | `vitest-node24-compatible-full.log/json` |
| 原生Vite HTTP启动及路由fallback | 6/6请求200 | `http-probe-results.json` |
| 真实Vite→API集成链 | **10/10通过** | `proxy_integration/results.json` |
| 全新严格Python锁环境启动API | FAILED：顶层导入缺少pypdf | `proxy-integration-lock-startup-failure.log` |
| 真实浏览器UI | 平台阻塞，未执行 | `browser-block.txt` |

注意：152指JSON `testResults.length`，不是`numTotalTestSuites=304`。151个`.test.ts/.tsx`加1个`media-url-policy.test.js`均实际执行；没有漏掉原有前端单元测试文件。逐文件和逐用例名称见152行 `frontend-test-matrix.csv`。

### 2. 测试环境与可重现命令

- Node.js：v24.19.0；npm：11.9.0。
- 仓库声明的包管理器：`pnpm@9.15.9`；本环境实际pnpm：11.19.0。
- 依赖按原始锁文件解析，211个包来自已有缓存，没有自主升级版本。
- 实际安装版本包括React19.2.8、React Router7.18.2、Vitest2.1.9、Vite6.4.3。
- `pnpm install --frozen-lockfile`完成依赖链接后，pnpm11因默认忽略esbuild构建脚本返回`ERR_PNPM_IGNORED_BUILDS`，并自动在`pnpm-workspace.yaml`添加`allowBuilds`提示。本次不将此作为产品BUG；原始文件由主任务恢复，改写副本作为环境证据保留。后续通过npm执行同一个仓库脚本，不触发pnpm11的自动重新安装；esbuild原生包可正常运行，生产构建通过。

生产构建命令（工作目录`repo`）：

```bash
npm --prefix apps/web run build
```

原始全量组件测试：

```bash
npm --prefix apps/web test -- --maxWorkers=2 --minWorkers=1   --reporter=default --reporter=json   --outputFile=/workspace/scratch/8ec6a0a84105/evidence/ui_audit/vitest-final.json
```

诊断并修正测试环境后的全量组件测试：

```bash
npm --prefix apps/web test -- --maxWorkers=2 --minWorkers=1   --environment=/workspace/scratch/8ec6a0a84105/evidence/ui_audit/node24-jsdom.mjs   --reporter=default --reporter=json   --outputFile=/workspace/scratch/8ec6a0a84105/evidence/ui_audit/vitest-node24-compatible-full.json
```

`node24-jsdom.mjs`是独立测试环境适配器，仅在jsdom安装全局DOM对象之前保存Node原生AbortController/AbortSignal，然后恢复给Node原生Request使用。未修改业务代码、未放宽业务断言、未抹掉取消信号，也没有绕过浏览器访问限制。它只用于解释并隔离测试进程的跨realm类型兼容性。

#### 原始失败的判定

原配置中的6个导航失败分布于`SystemPages.test.tsx`（3项）、`router.test.tsx`（2项）、`featureFlags.test.tsx`（1项）。同时出现：

```text
TypeError: RequestInit: Expected signal ("AbortSignal {}") to be an instance of AbortSignal.
```

这些文件在适配环境下31/31通过，随后相同环境全648项通过。因此不能据此宣称产品导航损坏。原配置另一失败是`EpisodeReviewWorkspace.test.tsx`中的审核复选框用例；该文件独立重跑9/9通过，兼容全量也通过。目前只能记为初次并行运行中的一次不稳定，尚不能定性产品缺陷。

建议将工具链兼容性列入测试工程任务：明确支持的Node版本，CI使用仓库声明的pnpm版本，并让Request/AbortController/AbortSignal使用一致realm。若选择更新测试框架，先验证取消请求、路由离开、超时和草稿保护语义，不能简单删掉signal以消除报错。验收要求是在声明支持的Node版本上原生测试命令稳定通过，并保留取消相关断言。

### 3. 真实HTTP启动与前后端集成

#### 3.1 Vite静态服务

原生`dev`脚本的`--host 0.0.0.0`在本测试平台调用`os.networkInterfaces()`时发生`uv_interface_addresses returned Unknown system error 1`。这是平台网卡枚举限制。用原生CLI覆盖为`--host 127.0.0.1`即可正常启动，不需要更改Vite源配置。

`http_probe.py`在同一测试进程中启动Vite并发HTTP请求，验证以下6项均为200：

| 路径 | 验证内容 |
|---|---|
| `/` | HTML、root挂载点存在 |
| `/src/main.tsx` | 返回JavaScript、MIME正确 |
| `/projects` | SPA历史路由fallback |
| `/story-adaptation` | SPA历史路由fallback |
| `/quick-create` | SPA历史路由fallback |
| `/production-factory` | SPA历史路由fallback |

这些请求确认开发服务器可启动、能提供入口与路由页面，**不代表React已在真实浏览器渲染成功**。

#### 3.2 真实Vite→API→SQLite小说导入链

`frontend_proxy_http.py`使用独立新实例、新SQLite、真实迁移、真实FastAPI/uvicorn、真实Vite代理。HTTP请求发往5173端口，`/api`经仓库原生Vite代理进入18765后端。没有伪造API返回或模型输出。

最初使用严格`requirements.lock`环境时，API尚未开始监听就失败：`main.py`→`service_composition.py`→`documents.py:18`顶层导入`pypdf`，但锁环境没有该包。此项是实证启动缺陷，主后端报告已有具体修复。为继续验证后续链路，之后改用主任务已补齐依赖的测试环境；这一继续测试不代表锁安装问题已修复。

补依赖后10项全部通过：

1. 获取Vite首页HTML。
2. 经Vite获取API live状态。
3. 经Vite获取API ready状态。
4. 经Vite完成实例会话bootstrap；证据中的临时令牌已脱敏。
5. 读取新数据库项目列表，确认为空。
6. 经Vite创建原创测试项目，配置2集、每集120秒、9:16、480×854、24fps，允许暂未配置生成能力。
7. 经Vite使用二进制请求体上传原创UTF8小说《钟楼来信》，包含6章、916字符（含标题及换行）；中文文件名使用百分号编码传入。
8. 读取后端抽取的小说段落。
9. 使用真实preview_hash提交小说解析结果。
10. 读取项目分集目录。

数据库迁移输出`integrity=ok`。原始JSON包含响应结构、状态码、耗时；源码与运行日志都在`evidence/ui_audit`下。模型权重与本机manifest缺失使启动同步记录错误，未影响本次明确列出的项目/文本导入操作；没有宣称图像、配音、视频或成片生成已在该环境完成。

### 4. UI/交互缺陷与源码审查

具体当前版本缺陷、精准文件行号、触发条件、修复方案和验收标准由独立前端源审报告给出：`notes/frontend_source_audit.md`。其独立测试放在`evidence/frontend_repros`，直接渲染当前生产React组件并操作DOM；测试断言的是缺陷存在，因此“专项用例通过”表示**复现成功，不表示修复完成**。

重点覆盖包括：一键制作的小说上传竞态、未提交正文丢失、失败错误展示，公共Dialog/Drawer焦点与嵌套Esc，时间线音轨设置与dirty状态/冻结逻辑，媒体回到开头的seek状态，以及配置页级联选择。现有648项通过没有覆盖这些新增对抗场景，不能把全量绿灯当成产品无缺陷。

`src/pages`目录下23个页面模块（另外的feature工作区按路由表补充）与53份CSS的静态覆盖清单见子报告及其配套清单；页面实际像素布局、字距、真实溢出、不同缩放、真实滚动和触控可用性仍需要后续浏览器截图验证。

### 5. 浏览器阻塞与未完成项

已按当前浏览器技能连接官方提供的浏览器，读取全部操作及文件规则。第一次本地可达性探测是访问独立临时静态测试服务`http://127.0.0.1:18766/`。浏览器返回：

```text
Browser Use could not complete this action because a browser security check was unavailable.
The permission request could not complete, so access was not granted.
Cloud browser could not obtain an approval decision ...
Browser Use is failing closed; no explicit denial was made.
... must not bypass browser security controls or use an indirect workaround.
```

随后浏览器地址仍为`about:blank`。官方故障恢复说明没有提供可修复审批链的步骤。没有通过改域名、改端口、换浏览器引擎或另起Playwright规避这一限制；后续HTTP测试属于独立接口测试，没有控制浏览器。

**真实浏览器完成数为0，不存在可交付的真实UI截图。**以下项目应放入后续Windows/具备合法浏览器访问的环境验收清单，不能勾选为本轮已测：

- 从首页开始逐页真实点击、弹窗、上传小说、切项目与分集、刷新和前进后退。
- 全流程实际模型调用后的资产图、视频、声音、字幕和最终成片在浏览器中的显示/播放。
- 1440×900、1280×800、1024×768及窄窗口的截图对照；水平溢出、文本截断、滚动定位、弹窗尺寸。
- 真实键盘焦点、中文输入法、长段粘贴、拖放文件、多个弹窗、浏览器刷新恢复。
- 真实媒体seek/播放/暂停/同步、音频设备和导出下载。

后续验收应优先用独立缺陷复现报告中的步骤在真实浏览器复核，然后对新库完成“建项目→导入小说→分集/分镜→资产→音视频→审核→成片→导出”，每一步保留任务ID、HTTP关联ID、截图及最终媒体摘要。需明确区分真实模型结果、测试夹具结果和单纯组件mock结果。


## 附录G：当前页面、交互、布局源码与专项复现

### 1. 审计基线、范围与结果解释

- 审计提交：`8a63c604a1a13556dbe277d312ecf73bebb52883`（本次获取的 main）。下文行号全部以该提交为准；仓库根目录为 `/workspace/scratch/8ec6a0a84105/repo`。
- 本专项负责当前可达前端路由、状态管理、接口调用与错误恢复、审核交互、时间线、通用浮层、键盘操作、响应式源码。未修改仓库源码。
- 静态清单覆盖 `apps/web/src/pages` 下全部 **23 个页面文件**，另外检查路由直接引用的改编规划与 Visual Lab 工作区；建立 **53 份 CSS** 清单，其中静态导入图可达 44 份。导入图共 278 个非测试 TS/TSX/JS/CSS 文件，232 个可达。清单仅证明审查范围，不能替代渲染或端到端测试。
- 使用真实项目组件和真实前端客户端函数、React、React Query、Testing Library、Vitest 2.1.9 与 JSDOM 构建独立复现。接口读写结果采用明确构造的成功、失败或延迟响应；这部分**不是后端端到端验证**。
- 最终独立复现：**5 个测试文件、14 个用例全部成功复现缺陷，进程退出码 0**。最终运行开始于 2026-09-21 17:09:07 UTC（按 JSON startTime 换算），耗时 12.52 秒。
- **这些测试主动断言当前的错误行为，`14 passed` 表示问题复现成功，绝不表示项目已修复或功能全部通过。** 修复时应先把断言改为期望行为，确认旧实现失败、新实现通过。
- 真实浏览器交互受到本次浏览器安全审批基础设施故障阻断，本专项未执行真实截图、像素测量、浏览器音视频连续播放或辅助技术实测。没有把样式断点、JSDOM DOM 结果写成“所有尺寸视觉通过”。
- 父任务另行执行原项目全部测试、构建与真实前后端连通性；其结果应在总报告中独立列示，不与本专项缺陷复现计数混合。

证据目录：`evidence/frontend_repros/`。核心文件为 `run.log`、`test-results.json`、5 个 `*.defects.test.tsx`、`vitest.config.mjs` 与 `frontend_static_inventory.json`。独立用例直接 import 被审计仓库源码；测试中的 API mock 与 CapabilityPicker 替身仅隔离外部依赖，没有重写受测组件的业务逻辑。

#### 优先级约定

P1 表示会丢失用户工作、让当前可见设置与冻结/生成行为不一致，或在项目支持的部署模式下阻断重要工作流；P2 表示明确的功能、错误恢复、可达性或键盘交互缺陷。本专项没有确认前端 P0。

| 编号 | 优先级 | 问题 | 证据等级 |
|---|---|---|---|
| FE-01 | P1 | 延迟上传结果清空用户刚粘贴的新原稿 | 组件动态复现 |
| FE-02 | P1 | 多个主要编辑器未接入现有未保存草稿保护 | 原稿与时间线动态复现，其他编辑器源码确认 |
| FE-03 | P2 | 全剧规划重试、影响预览、正式应用的失败静默 | 3 个组件动态复现 |
| FE-04 | P2 | 查询失败被呈现为没有任务/没有数据 | 全剧规划动态复现；生产工厂与设置源码确认 |
| FE-05 | P1 | Dialog/Drawer 父组件更新后抢走输入焦点 | 2 个通用组件动态复现，身份审核实际调用点确认 |
| FE-06 | P2 | 嵌套浮层按一次 Esc 同时关闭内外层 | 通用组件动态复现，模型配置嵌套调用点确认 |
| FE-07 | P1 | 时间线音轨/字幕开关不计入 dirty，可冻结旧设置 | 组件动态复现 |
| FE-08 | P2 | 时间线“回到开头”只改游标，不改变视频播放位置 | 组件动态复现 |
| FE-09 | P1（HTTP LAN 场景） | 多处无降级调用 crypto.randomUUID，提交前直接报错 | 3 个真实客户端函数动态复现、其余调用点源码确认 |
| FE-10 | P2 | QC 的“仅项目级/仅分集级”空选项会被自动改回首项 | 组件动态复现 |
| FE-11 | P2 | 项目、整剧交付、集中审核等列表被固定首屏截断 | 前后端分页契约与 UI 源码确认 |
| FE-12 | P2 | 生产会话创建成功、启动失败后缺少原会话启动恢复 | 前端调用顺序与后端状态契约确认 |
| FE-13 | P2 | 空资产库第一次手动创建失败时没有错误信息 | 条件渲染路径确认 |
| FE-14 | P2 | Visual Lab 搜索 Esc 提示与行为不一致，创建模态缺少键盘管理 | 当前路由源代码确认 |

### 2. 确认问题、修复方案与验收

#### FE-01：上传请求完成后会清空另一种来源的新原稿

**位置**：`apps/web/src/features/pipeline/OneClickPipelineWorkbench.tsx:270–301`，重点为 `288–295`；来源切换按钮 `367–373`、正文输入 `413`。

**触发步骤**：选择文件 A 并开始上传；在上传返回前切换至“粘贴正文”；输入或粘贴正文 B；让文件 A 的上传响应返回成功。

**实际结果**：B 被清空。动态用例 `FE-01 pending file upload destroys newly pasted manuscript text` 成功复现。上传函数在 await 之后无条件设置 `activeSource`，继而执行 `setRawText("")`；来源标签与正文仍可操作，也没有以当前来源意图或请求序号过滤过期响应。

**影响**：丢失用户刚准备好的小说/剧本；较慢解析、较大文档和网络延迟会放大触发窗口。这里是前端状态覆盖，不等同于服务器删除了已持久化文档。

**修复方案**：为每次选择/上传生成来源意图 ID，只有响应仍属于当前意图时才能变更当前来源；切换模式递增意图版本并按能力取消旧请求。粘贴正文和文件选择分别保存草稿，上传成功不要无条件清空粘贴草稿。若产品选择禁止并行编辑，需同步禁用所有会改变来源的入口，并明确显示正在解析。

**验收**：受控延迟下 A 上传、切到 B、A 返回后 B 完整保留；依次选 A/B 时仅 B 生效；切换项目、卸载组件、上传失败重试均不覆盖其他上下文的草稿；中文、换行、超长文本内容逐字符一致。

#### FE-02：全局离开保护已经存在，但主要编辑器没有注册

**公共依据**：`apps/web/src/layouts/AppShell.tsx:73–75,145,168–172` 仅根据 `draftRegistry` 中的 dirty 状态触发导航和关闭页签保护；`335` 给 Outlet 的 key 同时包含 pathname 和 search，查询参数变化也会卸载页面内容。

**已动态复现的两处**：

1. `OneClickPipelineWorkbench.tsx:90–95,413` 的原稿保存在局部 `useState`。输入正文后 `draftRegistry.getDirty()` 为空；卸载再挂载，原稿为空。用例 `FE-02 unsubmitted pasted manuscript has no draft protection and disappears on remount`。
2. `features/edit-v2/EpisodeEditWorkspace.tsx:22–44,49,71` 已显示“存在未保存编排”，但 registry 中仍无 dirty owner。用例 `FE-02b changing timeline duration has no shared navigation draft protection`。

**同类源码确认**：`features/asset-bible-v2/AssetDescriptionEditor.tsx:9–25` 仅使用局部 draft；`pages/AssetBiblePage.tsx:76–82,157,188` 切换资产修改 URL，并以 asset ID 给编辑器 key。现有 `AssetBiblePage.test.tsx` 甚至明确验证切换角色后未保存文本被丢弃；这能防止把 A 的内容显示到 B，但缺少“离开前处理 A 的编辑”的产品保护。`features/production/SubtitleRevisionPanel.tsx:27–44` 局部字幕草稿挂在 `EpisodeEditWorkspace.tsx:73` 的无 dirtyGuard 抽屉中。`features/audio-v2/EpisodeAudioWorkspace.tsx:127–139` 混音字段也在切换 track 时重新初始化。

**影响**：导航、切换资产/音轨或关闭字幕抽屉会失去未保存工作；全局壳层看似支持保护，实际覆盖不完整。原稿/时间线已执行组件复现，资产/字幕/混音是直接源码结论，未冒充逐一完成真实浏览器导航。

**修复方案**：统一注册 ownerId、entityKey、baseline、dirty、save、discard；每个编辑器在更新和卸载时正确维护 registry。草稿以项目+实体+版本隔离；原稿较长时提供 IndexedDB 恢复。切换实体、查询参数、抽屉关闭同样先处理当前草稿。收到后台新版本时，dirty 编辑器应提示冲突，不应直接重新初始化用户输入。

**验收**：对原稿、资产描述、剪辑、字幕、混音分别验证保存并离开、放弃并离开、取消离开；保存接口失败不得离开；切换项目不串数据；刷新恢复遵循明示策略；同页切换实体与抽屉关闭也受保护。还应覆盖保存时重新输入、延迟保存响应和版本冲突，避免新编辑被旧保存回调清空。

#### FE-03：全剧规划关键操作失败后没有用户可见错误

**位置**：`OneClickPipelineWorkbench.tsx:204–246` 定义 retry、apply、previewApply mutations；失败重试按钮 `467`、正式应用/预览按钮 `607,611`。组件没有渲染这三个 mutation 的 error。

**动态证据**：分别让 retry、preview 和 apply API reject 特定错误；等待操作结束，按钮恢复可用，但错误标识与错误提示均不出现。三个用例 `FE-03a/03b/03c` 均复现。

**影响**：用户无法区分没点中、版本冲突、后端拒绝或网络错误；可能反复操作，并不知道 AI 结果是否已经写入。影响预览失败也没有可行动反馈。

**修复方案**：在每个操作附近显示持久的 `role="alert"` 错误，保留接口 code、用户可理解的 message、request ID 以及重试入口。409 应刷新当前 revision/impact 并再次确认；超时后先查询操作状态，避免直接再写。恢复成功或明确切换操作上下文时清理旧错误。

**验收**：三类请求分别覆盖网络中断、409、422、500；失败保留草稿与当前选择；错误可被键盘和读屏发现；重试传入刷新后的 revision/hash；实际已提交但响应丢失时不会重复创建。

#### FE-04：读取失败被错误解释为“还没有数据”

**动态位置**：`OneClickPipelineWorkbench.tsx:119–146`。`showConfig` 使用 `!latestQuery.isPending && !run`，没有排除 error；`362–455` 据此显示新建配置表单。

**复现**：给已有任务查询返回错误，页面显示“选择小说或剧本文档”等新项目输入；没有展示加载失败。用例 `FE-04 loading existing pipeline failure is misrepresented as an empty new project`。

**相同源码路径**：`pages/ProductionFactoryPage.tsx:45–55,226,237` 不区分 sessions/review 查询失败，分别显示“还没有生产会话”或空审核区域。`pages/ProductionSettingsPage.tsx:45–48,65–87` 中 configuration 失败会使配置快照消失，rights 失败传空列表，project 读取失败使模板复制入口消失。

**影响**：用户看到与持久数据事实不符的空状态；已有任务和待审内容的可见性下降。这里没有断言错误空页必然导致后端重复记录。

**修复方案**：显式区分 loading/error/success-empty/success-data。失败状态保留已知数据与上下文，并标记过期，提供局部重试。配置/权利读取失败不能通过 `?? []` 表达成没有记录。

**验收**：已有数据和首次读取场景各注入错误；不得显示“尚未创建”；重试恢复相同 run/session/setting；背景 refetch 失败保留此前内容并提示过期；selectedRun、sources 读取也遵循一致状态规则。

#### FE-05：通用 Dialog/Drawer 在父组件更新时重置输入焦点

**位置**：`components/ui/primitives.tsx:300–336`、`391–431`。safeClose 依赖 onClose，打开时聚焦关闭按钮的 effect 又依赖 safeClose。父组件使用内联 onClose 且自身受控输入更新时，回调身份改变，导致 effect cleanup/重建，再次 focus 关闭按钮。

**动态复现**：在真实 Dialog 和 Drawer 中分别放置父组件控制的 textarea；先聚焦输入，触发一次文字变更，`document.activeElement` 立即变成“关闭”或“关闭抽屉”按钮。用例 `FE-05a/FE-05b`。

**实际业务调用点**：`features/asset-bible-v2/CharacterIdentityPackPanel.tsx:477–484` 身份包批准说明与废弃原因在父组件持有 state，并传内联 onClose；`472–474` 参考图片抽屉也因选择更新父组件。审核说明是完成身份包批准的必填条件，因此不只是次要外观问题。

**影响**：连续键入被打断，中文输入法尤其需要实际浏览器验收；用户可能不得不逐次重新点击输入框。本文确认的是 DOM 焦点变化，没有伪称已完成真实 IME 测试。

**修复方案**：打开/关闭生命周期的焦点 effect 只跟随浮层 open/session；在 ref 中读取最新关闭逻辑，事件监听可以稳定注册。仅首次打开执行初始焦点，关闭时恢复触发元素；焦点不能因数据轮询或任意父渲染移动。不要只在个别页面包 useCallback 掩盖公共组件问题。

**验收**：批准说明、废弃原因和任意受控输入连续键入保持焦点、光标、选区；查询刷新、loading 变化不抢焦点；初次打开/最终关闭的焦点恢复正确；补中文拼音/组合输入以及 Tab/Shift+Tab 的真实浏览器测试。

#### FE-06：嵌套浮层的 Escape 同时处理内外层

**位置**：`components/ui/primitives.tsx:311–331,403–426` 每个浮层都给 window 注册 keydown。Dialog 没有顶层判断；Drawer 的 stopPropagation 也不能自动阻止同一 window 上其他监听器执行。

**动态复现**：打开嵌套的两个真实 Dialog，在内层触发一次 Escape，内外层 onClose 均调用一次。用例 `FE-06 nested dialog Escape closes outer and inner dialogs together`。

**当前调用链**：`pages/ModelsPage.tsx:199–205` 的全屏执行配置契约中包含 ProfileConfigurationPanel；`features/profiles/ProfileConfigurationPanel.tsx:808–823` 再打开探测确认 Dialog。

**影响**：用户意在关闭当前确认层，却同时离开外层工作区；存在草稿时可能触发不必要的确认或关闭流程，焦点恢复也可能相互干扰。不能宣称每个父层都必然无提示丢草稿，因为 ModelsPage 自身传了 dirtyGuard。

**修复方案**：维护统一浮层栈，只允许最上层处理 Esc、Tab、背景关闭与焦点回收；底层设 inert/适当可访问状态。修复应与 FE-05 一起落到组件基础层。

**验收**：Dialog→Dialog、Dialog→Drawer、Drawer→Dialog 三种组合，一次 Esc 只关闭顶层；dirty 拒绝关闭保留当前栈；关闭内层后回到外层触发按钮；两层滚动锁引用计数正确。

#### FE-07：时间线的音轨和字幕选项不参与 dirty 判断

**位置**：`features/edit-v2/EpisodeEditWorkspace.tsx:15,27–30,38–49,53–57,71`。signature 只序列化视频 clips；对白、BGM/SFX、模型原声、字幕是独立 state。save 会发送四个选项，但 dirty 与 discard/freeze 门禁忽略它们。

**动态复现**：加载可冻结的草稿，取消“对白”；“放弃调整”仍禁用，“冻结当前草稿”仍可用；确认冻结仅调用既有 revision 的 freeze，未先 createDraft。用例 `FE-07 disabling dialogue is not dirty and allows freezing the previous saved draft`。

**影响**：用户看到对白已关闭，却冻结了服务器原有草稿配置。勾选状态与持久快照不一致；discard 也不能恢复这些设置。初始化始终用固定默认值，未从版本快照回填这些选项，应一并修正读契约。

**修复方案**：定义完整 TimelineEditorSnapshot，把 clips、四个开关和必要时间线参数全部纳入 baseline/hash。加载版本时从服务端获取并回填；dirty 时禁止冻结，或明确执行“保存并冻结”并冻结刚创建的 revision。discard 恢复完整快照。

**验收**：分别改变四个开关，每项都进入 dirty、可放弃、禁止直接冻结旧版；保存后重新加载保留设置；放弃恢复；冻结后的服务端快照、UI 与导出音轨/字幕一致；增删/排序/裁剪 clips 原有行为不退化。

#### FE-08：时间线播放游标与实际视频没有双向同步

**位置**：`EpisodeEditWorkspace.tsx:68,70`。“回到开头”仅 `setPlayheadUs(0)`；TimelineLanes 的 onPlayheadChange 同样只更新 state。video currentTime 仅在 loadedmetadata 中设置，timeupdate 是媒体到游标的单向同步。

**动态复现**：将视频 currentTime 设为 0.7 秒，触发 timeupdate，再点击“回到开头”；DOM video.currentTime 仍为 0.7。用例 `FE-08 Return to beginning moves the visible cursor but does not seek video playback`。

**影响**：时间显示跳回开始而视频仍在旧位置，下一次 timeupdate 又会把游标拉回。源码还显示点击时间标尺不负责媒体 seek；裁剪区间末端目前主要夹紧游标，没有完整的片段末端暂停/切换逻辑。这些延伸应以真实播放器验收。

**修复方案**：统一 `seekToTimelineTime(t)`，根据时间找到片段并换算 `source_start_us + offset`；必要时切换 video src，等 metadata 后执行待处理 seek。时间线 transport 与标尺使用该入口；明确单镜预览还是全时间线连续预览，并让按钮文案/行为一致。

**验收**：回到开头同步首镜和源入点；同镜标尺跳转、跨镜跳转、非零 source_start、裁剪终点、暂停/播放切换都与游标一致；两个镜头使用相同媒体版本时也要验证选择切换；真实浏览器验证 seek 精度与加载竞态。

#### FE-09：HTTP LAN 环境缺少 randomUUID 时多种功能在请求前失败

**部署依据**：README.md:125,136 明确存在 LAN_SERVICE，由 API 在局域网地址直接托管前端。浏览器的 Crypto.randomUUID 属于安全上下文能力；普通 HTTP 局域网地址通常不具有它，localhost 的安全上下文例外不能推导为远程 LAN 也支持。参考 [MDN：Crypto.randomUUID 的安全上下文要求](https://developer.mozilla.org/en-US/docs/Web/API/Crypto/randomUUID)。

**动态复现**：提供 `crypto.getRandomValues`，令 randomUUID 为 undefined；调用当前真实 `submitAssetMultiView`、`runVisualLabNode`、`cloneJob`，均在网络请求发出前 TypeError，fetch 调用数 0。用例 `FE-09 randomUUID absence prevents multiview, visual-lab, and job cloning before any request`。这模拟准确的缺失能力，不是远程真浏览器验收。

**已核实调用点**：

- `features/asset-bible-v2/multiviewClient.ts:110`：多视图提交。
- `features/visual-lab/client.ts:32`：节点运行。
- `generated/api.ts:866`：任务克隆；`1579,1583,1620`：自动化客户端、Webhook、工作流运行的默认幂等键。
- `pages/ProjectDeliveryPage.tsx:296,303,368`：超分批次、超分预览、正式交付。
- `features/episode-production-v2/EpisodeProductionWorkspace.tsx:245`：本集生产命令键。
- `features/model-platform-v2/ModelPlatformCenter.tsx:722`：能力烟测。
- `features/director-v2/DirectorSoundInspector.tsx:80,127`：对口型与声音命令。

`detailClient.ts:22`、`expressionClient.ts:28` 也含同类调用，但对应旧 UI 当前不可达，不额外声称是当前可点击入口。OneClickPipelineWorkbench 和部分批量 helper 已有降级逻辑，不能笼统写“所有功能都坏”。

**修复方案**：统一导出 command ID 工具，优先 randomUUID，缺失时使用 getRandomValues 实现符合 UUID v4 格式的生成；所有前端调用共享。修改生成脚本 `scripts/generate_client.py` 后重新生成客户端，不能只手改 generated/api.ts。幂等键的生命周期另外绑定用户操作与规范化请求，不应每次网络重试重造。安全 token 与普通操作 ID 的语义要保持清楚。

**验收**：HTTPS、本机 HTTP localhost、受支持的 HTTP LAN 三种环境；randomUUID 缺失但 getRandomValues 存在的自动测试；关键功能请求确实发出且 ID 格式合法；超时重试复用同操作键，修改 payload 使用新操作键。

#### FE-10：QC 空选项被自动默认选择逻辑覆盖

**位置**：`features/qc-policy-v2/QcPolicyManager.tsx:43–45,90`。effect 将任意 falsy 的 episodeId/shotId 设为第一项；下拉同时提供 value="" 的“仅项目级”和“仅分集级”。

**动态复现**：准备一个季度、一集、一个镜头；选择“仅项目级”，组件立刻又选回第一集和第一个镜头；选择“仅分集级”，又选回第一个镜头。用例 `FE-10 project-only and episode-only options immediately reselect first descendants`。

**影响**：无法按选项文案查看仅上层范围的生效策略；继承解析容易让人误以为在查看项目默认，实则携带分集/镜头上下文。写入 ownerType 是独立字段，不能把此问题误写为一定写入错误层级。

**修复方案**：区分“尚未初始化”与用户明确选择空值，可用 undefined/null/空串的明确类型或初始化标记。首次默认选中一次；用户清空后保持清空，禁用/清空下级选择；上级切换仅重置属于该上级的下级。

**验收**：空季度、多个季度、多个分集/镜头与 deep link 初始目标；空选择稳定保留；resolve 请求无多余 episode_id/shot_id；写入层级与查看继承层级文案清楚；上级变化不引用旧下级。

#### FE-11：多个列表只读取固定首批数据且没有完整浏览入口

**位置与实际边界**：

| 入口 | 源码位置 | 当前行为与影响 |
|---|---|---|
| 项目列表/首页/全局项目切换 | `pages/ProjectsPage.tsx:22–32,71–89`；`HomePage.tsx:77,85–86`；`layouts/AppShell.tsx:123` | 固定 limit=100；项目列表搜索仅过滤已返回 items，无下一页。第 101 个之外的项目不能通过当前列表搜索发现。首页计数也取 items，不能充当真实总数。 |
| 项目数据维护 | `ProductionSettingsPage.tsx:45,87` | 通过 listProjects(limit100).find 查当前项目；合法但不在首批的项目会缺少模板复制操作。应直接按 ID 读项目。 |
| 整剧交付分集/版本选择 | `ProjectDeliveryPage.tsx:217,443,484` | 固定 limit=50，未消费 next_cursor；后续分集不可按列表翻页浏览。搜索是服务端过滤，因此知道名称时可能找得到；“全部符合条件”批处理由服务端选择，不应误报为所有批量操作都只能 50 集。 |
| 最近生产会话与集中审核 | `features/production-sessions/client.ts:55–60`；`ProductionFactoryPage.tsx:226,237–257` | 会话只取 50；审核只取 100，虽审核契约有 total/next_cursor，页面没有翻页。更多待审分集无法从本区域继续浏览。会话标题叫“最近”，限制本身可接受，但必须有完整历史入口。 |

前后端分页字段依据：`generated/api.ts:511` 的项目列表返回 page；`2026` 整剧交付列表 page 含 cursor/limit/total/next_cursor；`86` ProductionSessionReviewPage 含 cursor/limit/total/next_cursor。后端 `apps/api/local_drama/api/routes/video_upscale.py:75–87` 接受 cursor。这是契约与 UI 连线缺口，本专项未向真实库创建数百条记录冒充压力测试。

**修复方案**：服务端搜索与游标分页统一落地；页面展示总数、已加载数和加载更多/分页入口；选择集合以实体 ID 维护，跨页保留。详情查询绝不依赖首屏列表。审阅工厂需要分页定位、未审核过滤与进度总览；API 类型保留 page 元数据。

**验收**：101 项目、51 交付分集、101 待审项；尾页可达、搜索跨全集、刷新页码恢复、不重不漏；跨页选择不丢；列表计数与服务器 total 对齐；删除最后一页项后可回退到合法页面。

#### FE-12：创建会话成功但启动失败后的恢复路径不完整

**位置**：`pages/ProductionFactoryPage.tsx:121–126` 顺序 await create、start，只有 start 成功后才保存/展示新 session ID 并 refresh。控制按钮 `231` 只处理 PAUSE、RESUME、CANCEL，没有 START。`features/production-sessions/client.ts:27–35` 每次 create/start 重新创建幂等键。

**触发条件**：create 已成功持久化 READY 会话，随后 start 返回网络错误或服务端拒绝。前端显示错误，但没有立即保留新会话上下文；再按“一键生成”会再次走 create。

**契约证据**：`apps/api/local_drama/application/production_sessions.py:242–250` 的 READY 合法动作含 START；同文件 `729–738` 调度恢复会跳过 READY；`788` 起的 start 显式处理 READY。

**实际后果边界**：缺少直接启动已有 READY 会话的正常 UI 路径，可能产生额外创建尝试。不是绝对死锁：当前后端可通过先 PAUSE 再 RESUME 的绕行恢复，因此不能报告成完全无法恢复。

**修复方案**：create 返回后立即缓存 session ID，并在失败时刷新/选中它；为 allowed_actions.START 提供“启动此会话”。重试首先查询原会话状态，区分已启动、待启动和终态，保留原用户操作幂等键。可考虑后端提供明确幂等的 create-and-start 命令，但不能把两次非幂等前端请求简单隐藏在一个按钮后。

**验收**：分别丢失 create 响应、丢失 start 响应、start 500、start 409；恢复原会话且不产生多余活跃生产；刷新/重启后 READY 有明确操作入口；操作反馈显示会话 ID 与真实状态。

#### FE-13：空资产库手动创建失败时错误没有渲染

**位置**：`pages/AssetBiblePage.tsx:95–109` 创建 mutation；`222` 渲染 create.error 的节点位于 `selected` 存在的详情分支；无资产时走 `228` EmptyState。手动创建表单位于该分支外 `232–237`，仍可提交。

**触发条件**：某种类资产列表为空，手动添加第一项，请求失败。

**实际结果**：添加按钮从 pending 恢复，输入保留，但错误节点位于未渲染分支，没有提示。此项为条件渲染源码确认，未新增动态测试。

**修复方案**：把 create.error 移到手动创建表单内，与主参考更新错误分离；错误绑定字段和表单说明，保留输入，重试前验证重名/字符规则。

**验收**：空库和有资产库分别注入 409/422/500/网络失败，均在创建表单旁显示错误；不会误指向当前选中资产；成功后关闭或清空表单、选中新资产且列表更新。

#### FE-14：Visual Lab 的搜索和创建浮层缺少完整键盘行为

**位置**：`features/visual-lab/VisualLabWorkspacePage.tsx:431–455,498,502`。全局 keydown 先 `isTextInput(event.target) return`；搜索输入 autoFocus，并显示 Esc 提示，所以聚焦搜索框时 Esc 不执行关闭。创建对象 form 虽声明 role=dialog / aria-modal=true，却没有共享 Dialog 的焦点陷阱；Escape 分支也没有 setCreateKind(null)。

**影响**：搜索框提示了无法按预期使用的 Esc；创建模态可通过 Tab 进入背景控件，不支持预期的 Escape 关闭。这里为源码确认，未进行真实辅助技术与焦点行走测试。

**修复方案**：先处理顶部浮层的 Escape，再处理应对普通文本输入屏蔽的画布快捷键；创建浮层复用修好的统一 Dialog，提供焦点陷阱、恢复与标题关联。搜索层是非模态，可以保持非模态设计，但要有明确关闭按钮和可靠 Esc 行为。

**验收**：焦点在输入、按钮、画布时 Esc 行为一致；组合输入不误触快捷键；模态 Tab 不进入背景；关闭后焦点回到打开入口；搜索结果选择后定位与关闭保持现有行为。

### 3. 所有页面与主要 UI 链路覆盖清单

以下“检查”均指本专项源代码与组件逻辑检查，不能读作真实浏览器逐页视觉通过。功能后端与完整生成链路证据由父任务整合。

| 页面文件（均位于 apps/web/src/pages） | 已追踪的主要功能与关联组件 | 本专项结论/关注点 |
|---|---|---|
| HomePage.tsx | 工作台指标、运行时准备状态、最近项目、新建向导 | FE-11；最近项目/空态/错误分支已读 |
| ProjectsPage.tsx | 创建、状态过滤、搜索、项目选择 | FE-11；搜索在本地首批执行 |
| ProjectHomePage.tsx | 项目概览、下一步、分集进度库、追加结构 | 查询状态和路由串联已读；未新增确定缺陷 |
| StoryWorkspacePage.tsx | 当前原稿上传/已有源/粘贴→全剧规划→影响预览→应用 | FE-01/02/03/04；实际挂载 OneClickPipelineWorkbench |
| AssetBiblePage.tsx | 人物/场景/道具、描述、主参考、多视图、身份包、声音 | FE-02/05/09/13；审核说明实际调用点确认 |
| EpisodePlanPage.tsx | 本集方案、重规划、执行预览、本集持续生产 | 追踪 EpisodeProductionWorkspace；FE-09 |
| DirectorDeskPage.tsx | 镜头导航、候选、生成 Inspector、采用、声音、原文、时间线预览 | 追踪当前导演组件；声音命令 FE-09，部分自定义 tab 见改进项 |
| AudioPage.tsx | 配音任务、混音轨道、音量/裁剪/淡入淡出 | 追踪 EpisodeAudioWorkspace；FE-02 |
| TimelinePage.tsx | 视频裁剪排序、音轨/字幕开关、保存冻结、版本、字幕与合成 | FE-02/07/08；3 个组件用例 |
| EpisodeReviewPage.tsx | 候选/成片证据、检查表、人工批准、状态与返工 | 追踪 EpisodeReviewWorkspace，保留机器 QC 与人工批准区分；父任务执行其既有测试 |
| DeliveryPage.tsx | 单集成片、交付、就绪检查、后处理、版本刷新确认 | source 状态与操作分支已读；共享浮层修复一并回归 |
| ProjectDeliveryPage.tsx | 整剧超分、预览、版本采用、人工审核入口、正式交付、清理 | FE-09/11；目标采用与人工门禁路径已读 |
| ProductionFactoryPage.tsx | 预算预检、创建启动、暂停继续、集中审核、返工和确认 | FE-04/11/12；人工批准条件未被简化成机器完成 |
| JobsPage.tsx | 项目标签、任务查询、轮询、重试/取消/克隆、错误详情 | 追踪 JobsPanel；克隆 FE-09，进度量纲见后续风险 |
| ModelsPage.tsx | 模型平台、Provider、本地 LLM、执行配置与探测 | FE-05/06/09；存在实际嵌套 Dialog |
| SystemWorkflowsPage.tsx | 工作流版本、运行环境、Comfy 工作区、配置 | 追踪共享配置与平台；外部运行时实测边界由总报告说明 |
| DiagnosticsPage.tsx | 诊断概览、容量、系统状态、审计历史、全局搜索 | 空态/错误/表格滚动源码检查，未新增确定缺陷 |
| ProductionSettingsPage.tsx | 生产、交付、自动化、权利、数据维护五个区段 | FE-04/09/11；列表详情混用与静默读取失败 |
| ProjectCapabilitiesPage.tsx | 生成偏好、项目与分集配置继承 | 追踪 GenerationPreferencePanel；需回归上下文切换与保存冲突 |
| QcPoliciesPage.tsx | 项目/分集/镜头规则、生效继承、阈值、重抽上限 | FE-10；组件动态复现 |
| DirectorRecipesPage.tsx | 导演配方、版本、关联 QC 策略 | 当前 Manager 读写与错误状态已读；没有把历史实现当当前入口 |
| QuickCreatePage.tsx | 快速文本/图像/视频试验与预检提交 | 追踪 QuickGenerationWorkbench；属于短提示词媒体工作台，不是原创长篇小说生成器 |
| WorkspaceShells.tsx | 项目/系统/分集工作区标题与 Outlet | 结合 AppShell/routeRegistry 检查导航、上下文、响应式壳层 |

另外直接可达的 `features/story-adaptation/AdaptationPlanningPage.tsx`、`features/story-adaptation/AdaptationPlanWorkspacePage.tsx`、`features/visual-lab/VisualLabListPage.tsx`、`features/visual-lab/VisualLabWorkspacePage.tsx` 也纳入源码检查；Visual Lab 确定问题见 FE-09/14。以上功能路径均相对 `apps/web/src`。

#### 实际路由入口核对

`apps/web/src/app/router.tsx:87–158` 是当前入口依据。23 是 pages 目录文件数，不能等同于页面路由总数；同一模块也可能承载多个 URL/设置区段。

| 实际 URL（项目路径以 /projects/:projectId 为前缀） | 当前入口 |
|---|---|
| `/`、`/projects`、`/quick-create` | HomePage、ProjectsPage、QuickCreatePage |
| 项目根、`factory`、`story` | ProjectHomePage、ProductionFactoryPage、StoryWorkspacePage |
| `story/plans`、`story/plans/:planId` | features 下 AdaptationPlanningPage、AdaptationPlanWorkspacePage |
| `assets`、`delivery` | AssetBiblePage（ASSET_BIBLE_V2 开关）、ProjectDeliveryPage |
| `settings/production`、`settings/delivery`、`settings/automation`、`settings/rights`、`settings/data` | ProductionSettingsPage 的 5 个区段 |
| `settings/capabilities`、`settings/directing`、`settings/quality` | ProjectCapabilitiesPage、DirectorRecipesPage、QcPoliciesPage |
| `labs`、`labs/:labId` | features 下 VisualLabListPage、VisualLabWorkspacePage |
| `episodes/:episodeId/plan` | EpisodePlanPage |
| `episodes/:episodeId/studio`、`episodes/:episodeId/studio/:shotId` | DirectorDeskPage（DIRECTOR_DESK_V2 开关） |
| `episodes/:episodeId/post/review`、`post/audio`、`post/edit` | EpisodeReviewPage、AudioPage、TimelinePage；后两个路径同样带 episodes/:episodeId 前缀 |
| `episodes/:episodeId/delivery`、项目 `models` | DeliveryPage、ModelsPage |
| `/system/capabilities`、`/system/jobs`、`/system/diagnostics`、`/system/workflows` | ModelsPage、JobsPage、DiagnosticsPage、SystemWorkflowsPage |
| 项目 `settings`、分集 `post`、`episodes/:episodeId/production` 及历史别名 | 显式重定向；旧 qc-policies/director-recipes/production-settings/operations/jobs/diagnostics/lab/canvas、分集 direct/generation/run/review/audio/timeline、全局 models/jobs/diagnostics/lab 保留迁移路径，未当成额外独立功能页 |

#### 14 个动态断言与缺陷映射

| 证据文件 | 用例数 | 断言对应 |
|---|---:|---|
| pipeline.defects.test.tsx | 6 | FE-01 上传覆盖；FE-02 原稿卸载丢失；FE-03a retry、FE-03b preview、FE-03c apply 错误不可见；FE-04 查询错误显示新建 |
| primitives.defects.test.tsx | 3 | FE-05a Dialog 焦点；FE-05b Drawer 焦点；FE-06 内外层同时响应 Esc |
| timeline.defects.test.tsx | 3 | FE-07 开关未进入 dirty 并冻结旧草稿；FE-08 游标复位但 video.currentTime 未变；FE-02b 时间线未注册草稿保护 |
| compatibility.defects.test.tsx | 1 | FE-09 缺少 randomUUID 时多视图、Visual Lab、任务克隆三个真实函数在 fetch 前失败 |
| qc.defects.test.tsx | 1 | FE-10 上层范围空选择被第一集/第一镜头覆盖 |

复现命令（在 `/workspace/scratch/8ec6a0a84105/evidence/frontend_repros` 执行）：`./node_modules/.bin/vitest run --config vitest.config.mjs --reporter=verbose --reporter=json --outputFile=test-results.json`。最终 JSON 的 numTotalTests=14、numPassedTests=14、numFailedTests=0；文件数按 Vitest 控制台与 testResults 数组统计为 5，JSON suite 数还包含 describe 分组，不应当作文件数。

### 4. 布局、可访问性与 UI 文案审查

#### 已确认的样式设计事实

样式清单记录每份 CSS 的行数、可达性、media query、overflow 规则和较大的固定 min-width。当前全局壳层、首页、模型配置、原稿、生产工厂、QC、项目交付与多数工作区均已有断点规则；大量 grid 使用 minmax(0,1fr)，表格、媒体带和时间线也有局部滚动。

共享 `components/ui/primitives.css:69–94` 为 Dialog/Drawer 设置视口内宽高和主体滚动；时间线内容有大于窄屏的 min-width，这是横向时间轴设计，需要结合父级 overflow 判断，不能仅凭 900px 判定页面溢出。`styles.css:420` 的审计表格 min-width=920 同样需要结合容器验证。未确认“所有页面完全响应式”，也未因孤立规则报未经验证的横向溢出缺陷。

#### 当前可直接修复的交互问题

FE-05/06 是公共焦点与浮层栈问题，影响正常文字录入和复杂设置。FE-14 是当前 Visual Lab 专用浮层实现与键盘提示不一致。优先修这些功能性 UI 缺陷，再做视觉微调。

一些工作区自行构造 role=tablist/button role=tab：`OneClickPipelineWorkbench.tsx:367–373`、`AssetBiblePage.tsx:141–147`、`QcPolicyManager.tsx:91`、`DirectorDeskPage.tsx:425`。与仓库现有 `primitives.tsx:479–579` 的 Tabs 相比，部分自定义标签缺少方向键/Home/End、roving tabIndex、aria-controls/tabpanel 关联。此项作为可访问性改进清单，未声称完成读屏实测或给所有 tab 一概判失败。优先复用统一 Tabs，并分别确认标签切换究竟是选项还是页面导航。

混音 Inspector、NLE-lite、Disposition、Selection Authority、Generation Intent ID 等技术词在面向创作者的 UI 中直接暴露。专家配置可保留技术术语并给释义；主流程宜显示“混音调整/当前画面/机器临时选择/绑定已有生成任务”等用户动作。文案评价属于易用性建议，不计为功能故障。

#### 真实视觉与辅助技术待验收矩阵

应在可用真实浏览器中补齐 1920、1440、1280、1024、768、390 CSS px 宽度，短高度与 125%/150% 缩放；数据包含超长中文标题、100+ 条资产/镜头、无媒体、404 图片、错误提示与完整审核说明。检查横向滚动归属、吸底按钮覆盖、侧栏展开、Drawer/全屏 Dialog、滚动锁、触摸目标、键盘焦点可见与页面焦点顺序。还需浏览器音视频播放、字幕时间对齐、seek、中文 IME 与读屏测试。此次没有相应截图证据，保持“未验证”。

### 5. 与小说/后端专项的关联及避免重复计数

- 当前全剧入口的 `source_coverage` 在 `OneClickPipelineWorkbench.tsx:496–507` 展示覆盖率/续接元数据，但没有供 SUCCEEDED/PARTIAL 运行继续处理剩余原稿的按钮；重试按钮在 FAILED 分支 `467`。后端专项已经真实复现长文截断和无法有效续接，应以其 NP 问题为主，本专项只补充 UI 恢复缺口，不另计一个重复问题。
- `features/projects/AIDraftReviewPanel.tsx` 与 `ScriptImportPanel.tsx` 在当前 main 的静态导入图中没有非测试调用。旧场次修订 API 的字段丢失由后端专项单独报告；不能把旧面板中的按钮当成当前已从浏览器验证的功能入口。
- 本次静态清单有 25 个不可达 TSX，包括旧资产批量/细节/表情、旧导演控件、旧 Breakdown/ScriptImport 等。这不自动证明产品缺功能，可能是迁移残留；评估历史文档或旧测试时应先确认 current route reachability。
- 未发现独立的“从提示词创作整部原创小说”当前入口。当前小说主流程是导入/粘贴已有原稿后生成改编规划、资产与后续制作内容。QuickCreate 的短提示词生成图像/视频不应被宣传或测试记录写成原创长篇小说生成。

### 6. 建议的修复实施顺序

1. 先修 FE-01/02/07：完整草稿模型、导航保护、异步请求意图和快照一致性。修复数据丢失前，不宜只美化保存按钮。
2. 同一基础组件变更修 FE-05/06，再回归身份审核、模型配置、媒体选择、字幕和时间线弹窗；Visual Lab 专用层随后接入。
3. 抽取统一 command ID 与错误状态组件，修 FE-03/04/09/13；生成客户端必须从生成源修。
4. 修时间线 seek、QC 选择初始化和生产会话恢复 FE-08/10/12；后端契约改动应更新前端类型与读写快照。
5. 实现 FE-11 的完整分页/搜索，补边界数据测试；最后做真实浏览器多尺寸与完整小说→审核→交付链路验收。

本专项提供的是可核验的当前缺陷和修复验收方案。原项目既有测试全绿、前端 build 成功，也不能排除这些由新增失败/竞态/边界场景揭示的问题。


## 补充：磁盘容量门禁的最终对照裁定

### 收口补充裁定：QUALITY 磁盘 gate 测试失败

## 结论

`test_capacity_disk_gates::test_episode_quality_gate_uses_shots_times_frozen_profile_take_estimate_and_zero_writes` 在本次补充 manifest 重跑中的失败属于**测试夹具选错 Profile 能力**，不能认定为实际磁盘门禁被绕过，**不新增 SS-11**。

原失败环境中，生产预检的总体状态已经是 `BLOCKED`，阻断原因包含 `PROFILE_CAPABILITY_MISSING`。测试只检查到磁盘子项为 `PASS`，忽略了其估算为 `UNKNOWN` 以及整体已经拒绝执行。

## 最小对照及证据

从后端重跑的合成 SQLite 复制出独立数据库，仅使用临时合成数据，不修改生产代码、不联系运行时。

| 条件 | 原失败夹具 | 唯一字段校正后的隔离对照 |
|---|---|---|
| 被 `_published_profile()[0]` 选中的已发布能力 | VIDEO_FIRST_LAST_FRAME | VIDEO_I2V |
| legacy project binding | 标注为 I2V_VIDEO，却指向首尾帧 Profile | 未改其他数据 |
| 原 VIDEO_I2V Profile | CANDIDATE_BLOCKED | 仍保持原数据；对照只校正被选中测试 Profile 的能力 |
| 逐镜有效 Profile | 缺失 | 正确解析到 VIDEO_I2V |
| 总预检 | BLOCKED | BLOCKED |
| Profile 子项 | BLOCKED / NO_COMPATIBLE_PROFILE | PASS |
| disk_bytes_per_take | null | 10000 |
| take_count | 4 | 4 |
| estimated_output_bytes | null | 40000 |
| required_free_bytes | 1（仅操作者阈值） | 40000 |
| free_bytes | 20000 | 20000 |
| 磁盘子项 | PASS / estimate_source=UNKNOWN | BLOCKED / FROZEN_PROFILE_RESOURCE_POLICY |

精确定位：

- `apps/api/tests/test_generation_variants.py:169-172` 的共享 `_published_profile` 使用 `list_profiles()[0]`，把 manifest 排序当成能力选择。
- `apps/api/tests/test_capacity_disk_gates.py:131-146` 给该返回值写入磁盘预算，再插入 legacy `I2V_VIDEO` binding，但没有确认被引用版本本身是 `VIDEO_I2V`。
- `apps/api/local_drama/application/episode_production_runs.py:314-349` 的生产路径按逐镜 canonical `VIDEO_I2V` 解析并拒绝错误能力。
- `episode_production_runs.py:630-644` 的磁盘计算在有效 Profile 存在时确实执行 `每 take 预算 × 镜头数 × 有效候选数`，本次单字段对照验证了 `10000 × 1 × 4 = 40000`。

材料：

- `evidence/state_security/disk_gate_adjudication.json`
- `evidence/state_security/disk_gate_adjudication.log`
- `harness/state_security/adjudicate_disk_gate.py`

## 修复与验收

修改测试 helper 按 canonical capability 精确选择所需 `VIDEO_I2V` Profile，在夹具中为其建立匹配的输入契约、工作流和发布状态；通过当前偏好接口建立生产真正消费的绑定。不能依赖列表第一项，也不应在生产中把真正首尾帧 Profile 改成 I2V。隔离对照中的单字段修改只用于定位原因。

保留预期计算：free=20000 时 required=40000 必须 BLOCKED，free=40000 时该磁盘子项可 PASS；两种情形均保证预检零写入。另加缺少有效 Profile 的场景，断言整体 BLOCKED、估算 UNKNOWN。UI 可以把未知估算子项写成“仅检查最低磁盘阈值，输出估算不可用”，避免误读为已经核验完整输出预算。

本轮仅增加这一组失败归因对照，不改变前述 26 个新增场景的统计，不把对照记作新的产品缺陷。



## 补充：LAN配置失败的最终对照裁定

### LAN 最后两项失败的独立裁定

基准提交：`8a63c604a1a13556dbe277d312ecf73bebb52883`。本任务只检查 LAN 配置入口与两个既有失败的前提；没有迁移数据库、启动 API/Worker、访问网络或修改产品源码。

## 1. 结论

**两个原始失败应归为测试前提未跟随当前安全合同更新，不新增 LAN 运行缺陷编号。** 当前受支持的机器 JSON 配置能明确启用可信 LAN，`Settings.from_env()` 也能正确合并该配置与现有环境变量，并正确推导工作目录下的 Comfy 输入/输出目录。

`LOCAL_DRAMA_TRUSTED_LAN_UNAUTHENTICATED` 确实没有环境变量映射，设置它为 `true` 不会替代机器 JSON 的接受声明。但当前 README/Windows 部署文档规定的是 `trusted_lan_unauthenticated=true` 配置项，没有承诺同名 `LOCAL_DRAMA_...` 环境变量。机器配置路径实际可用，原生 Host 也按机器配置执行同一接受门禁。因此，本次不能仅凭“自行构造的环境变量没有生效”就宣称 LAN 整体无法启动或存在新的安全绕过。

如产品希望以后提供完全通过环境变量部署的模式，可以明确新增该映射及文档；在当前合同下，这属于配置入口可用性完善建议，而不是这两个失败已经证明的产品运行 BUG。

## 2. 两个既有失败分别说明了什么

| 用例 | 当前代码及实际前提 | 裁定 |
|---|---|---|
| `test_from_env_reads_network_mode_host_roots_and_limits` | `apps/api/tests/test_server_deployment.py:85–100` 只设置 LAN 模式、host、路径和上传限制，没有配置明确可信 LAN 接受；`:91` 还使用 Windows 反斜杠拼工具目录，但`:100` 按当前平台的 `Path /` 结果断言 | 原始失败是缺少必要接受声明；补上后，在 Linux 还有测试路径写法不一致。应修测试配置隔离和路径构造 |
| `test_work_root_derives_comfy_roots` | 同文件 `336–342` 只设置 LAN、host、work_root，没有信任接受声明 | 被当前 LAN guard 正确拒绝。支持的 JSON 前提补齐后，两个 Comfy 派生路径均正确 |

同一测试文件的 `_lan_settings`（`24–37`）已经显式填写 `trusted_lan_unauthenticated=True`；`test_lan_service_requires_explicit_trusted_lan_acceptance`（`64–66`）又明确要求未接受时拒绝。这两条失败测试与本文件其余当前合同不一致，不能通过删除产品校验把它们变绿。

## 3. 当前产品配置合同

- `apps/api/local_drama/config.py:124` 默认接受值为 `False`；`135–143` 验证 LAN 必须明确接受，LOCAL_ONLY 则不能带可信 LAN 接受。
- `config.py:325–346` 先加载机器配置；`347–380` 等显式白名单再覆盖受支持的环境变量。该白名单没有 `trusted_lan_unauthenticated`，它也不是自动读取全部 `LOCAL_DRAMA_*` 的 `BaseSettings`。
- `apps/api/local_drama/bootstrap/config_loader.py:13–20` 定义 `network.trusted_lan_unauthenticated`；`190–198` 正确映射到 `Settings`。
- `apps/api/local_drama/bootstrap/resource_locator.py:44–63` 支持 `LOCAL_DRAMA_CONFIG` 定位机器配置文件，所以现有环境启动入口有可执行的配置路径。
- `README.md:124–136` 说明明确接受及网络模式；`docs/deployment/windows.md:99–109` 说明机器模式、host、可信 LAN 接受一起变更，Host 和 API 均拒绝缺少声明的 LAN。
- `packaging/windows/config.server.json:5–11` 自带 LAN 模式和 `trusted_lan_unauthenticated: true`；Linux server 模板当前默认为 LOCAL_ONLY，不能把其 `false` 当成 LAN 配置错误。

## 4. 独立子进程实际对照

执行脚本：`harness/lan_final_review.py`。每一组都在独立 Python 子进程调用真实 `Settings.from_env()`，先隔离已有 `LOCAL_DRAMA_*`，再提供表中明确参数；只打印无秘密的配置字段。共 **8 组，全部执行完成**，没有模型替身。

| 组 | 输入 | 实际结果 |
|---|---|---|
| L01 | 第一个原测试的环境参数，无机器配置/接受声明 | `ValidationError`，明确要求可信 LAN 接受 |
| L02 | L01 加上未支持的 `LOCAL_DRAMA_TRUSTED_LAN_UNAUTHENTICATED=true` | 仍被 guard 拒绝；未知 env 键没有映射 |
| L03 | L01 加上受支持 JSON 接受声明 | Settings 创建成功；Linux 工具目录保留字面反斜杠，与原测试的 POSIX 期望不一致 |
| L04 | L03 改用当前平台 `Path /` 拼工具目录 | 模式、host、数据/项目根、默认work根、2048MB限制、两工具目录全部符合该测试的意图 |
| L05 | 第二个原测试的环境参数，无接受声明 | guard 正确拒绝 |
| L06 | L05 加上受支持 JSON 接受声明 | `work_root/comfy-production/input` 与 `output` 均正确 |
| L07 | JSON 接受为true，另加未支持的 env=false | 仍读取JSON true；证明该 env 键没有覆盖语义，不是已支持的撤销入口 |
| L08 | 无 LAN 覆盖，无机器配置 | LOCAL_ONLY、接受false，正常创建 |

原始结果：`evidence/lan_final_review/results.json`；其中包含每组明确设置的测试变量、输出、耗时及4份当前源码 Git blob 摘要。结果中 `verification_complete=true` 只表示这些对照均执行和校验完成，不表示原来的两个 pytest 用例已经被修改或重新跑成 PASS。

两项原始 pytest 失败继续保留在原基线；本次8组配置诊断**不计入**后端“补充XML新增212/合并1563”的去重PASS统计。

## 5. 最小调整与验收方案

### 必要：修正测试前提与文档清晰度

1. 为两个环境读取测试建立显式临时 `LOCAL_DRAMA_INSTANCE_ROOT` 与 `LOCAL_DRAMA_CONFIG`，写入最小 schema v3 JSON，明确 LAN 模式、host 与可信 LAN 接受，避免依赖开发机已有 config。
2. 使用 `str(tmp_path / "tools")` 和 `str(tmp_path / "tools2")` 构造 `LOCAL_DRAMA_TOOL_FALLBACK_DIRS`，用分号连接；不要在跨平台测试中硬编码 Windows 路径分隔符。
3. 保留现有未接受/false必须拒绝的测试。目录派生本身与 LAN 无关，也可将纯派生测试使用 LOCAL_ONLY，再用一个明确配置的 LAN 对照确认行为一致。
4. 在 README 网络模式表旁明确写出 `LOCAL_DRAMA_CONFIG` 的 JSON 示例，说明当前接受声明来自 `network.trusted_lan_unauthenticated`；Linux部署说明也同步补上这项必要字段。

验收：两原测试在干净 Linux/Windows、没有任何机器配置的 CI 中符合各自意图；默认 LOCAL_ONLY 仍成功、未明确接受的 LAN 仍失败；接受后的路径/限制/Comfy派生值正确；测试不读取或修改真实机器 config。

### 可选：未来支持纯环境变量声明

如果产品决定提供 `LOCAL_DRAMA_TRUSTED_LAN_UNAUTHENTICATED`，应在API和原生Host两端定义同一映射、布尔解析和覆盖优先级，并记录所采用的配置来源。明确接受必须由操作者提供，不能根据 `NETWORK_MODE=LAN_SERVICE` 自动置true。不存在/true/false/非法值、JSON与env冲突、LOCAL_ONLY+true都应得到清楚且一致的结果。此增强需要明确产品决定，本轮没有把它计作已经确认的新运行缺陷。

## 6. 复跑

```bash
/workspace/scratch/0102188ff063/audit_quality_venv/bin/python \
  harness/lan_final_review.py \
  --repo /workspace/scratch/8ec6a0a84105/repo \
  --output /workspace/scratch/8ec6a0a84105/evidence/lan_final_review
```

没有修改 `notes/backend_tests.md` 或 `notes/state_security.md`，避免与其他最后裁定任务冲突。

