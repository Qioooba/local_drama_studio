# 整剧分集成片批量 AI 超分：开发测试与交付验收

版本1.1 · 2026-09-21 · **测试计划已进入实施；隔离自动化已执行，真实 NCNN/GPU 与人工画质验收仍待执行。**

配套：[详细开发设计](development-design.md)、[Sol实施交接](sol-handoff.md)。编号用于实现后的证据追踪，不表示当前已有对应测试代码。

当前证据摘要见 [实施状态](implementation-status.md)。自动化通过不等同真实模型验收；本文件中 GPU/E2E/人工层尚未运行的条目继续视为未通过，而不是 PASS。

## 1. 验收对象与分层

验证对象是“多集最终成片从选择、配置、入队到正式1080p交付”的完整业务。必须分清五种证据：

| 层 | 能证明什么 | 不能替代什么 |
|---|---|---|
| 纯函数/合同测试 | 几何、参数、hash、状态、输入校验 | 实际模型可运行 |
| 隔离SQLite/API测试 | 事务、幂等、归属、版本、审核和交付门禁 | GPU兼容、画质 |
| 真实FFmpeg测试 | 解码、时间基、声音、分块拼接和封装 | AI推理已发生 |
| 浏览器测试 | 多选、表单、状态、恢复、导航和操作结果 | 返回的文件一定真实超分 |
| 真实Windows/GPU/UAT | 指定引擎/权重实际推理、性能、画质及正式打包闭环 | 所有模型和所有显卡均兼容 |

测试执行必须使用隔离数据库、project/work/cache目录及独立端口。原用户项目只做明确选定的非覆盖派生验收，不对用户全剧自动运行压力测试。Mock adapter用于控制故障可以接受，但报告必须标`FAKE_ADAPTER`，不能记入真实模型验收。

## 2. 必备Fixture

| Fixture | 内容与用途 |
|---|---|
| F01 | 854×480、24fps、12秒、H.264/AAC；含帧号和色块，横屏主路径 |
| F02 | 480×832、24fps、12秒、H.264/AAC；竖屏补边验证 |
| F03 | 864×480、24000/1001fps、至少5分钟；首中尾闪光/音频标记，时间漂移 |
| F04 | 640×480、SAR1:1；4:3到1080画布，CONTAIN/COVER |
| F05 | 1920×1080已达标，另有相同几何但没有SR provenance的文件 |
| F06 | 无音轨、双音轨（不同language/default）、非MP4可copy音频各一项 |
| F07 | 已烧字幕/水印的交付视频 + 原SRT/ASS sidecar + 源COMPOSE；核验不重复烧录 |
| F08 | 最新COMPOSE未批准、旧版已批准；包被撤回；源hash变动；approval变更 |
| F09 | VFR、HDR/10bit、隔行、非零rotation、非1 SAR、多视频流、损坏/截断文件 |
| F10 | 201集元数据，其中130可选、部分跨页；同时有重名集、跨季同编号和跨项目数据 |
| F11 | 两个模型Profile、退役Profile、缺模型、错误hash、未注册handler、不同adapter参数合同 |
| F12 | 用户或团队提供的真实漫剧短片：面部、细线、文字、快动作、暗部、渐变、场景切换；用于人工画质 |

F01–F11可由脚本构建合成测试文件和合法登记链；不要手写APPROVED在生产库。F12注明文件来源和用途，证据只包含用户认可的短片/截帧，不把原始整剧上传外部服务。

## 3. 功能测试矩阵

### 3.1 来源、选择与几何

| ID | 输入/操作 | 必须结果 | 层 |
|---|---|---|---|
| T01 | 已确认交付包、已批准COMPOSE同时存在 | 默认选有效主交付视频，展示真实source ID/hash；可显式切COMPOSE | API/UI |
| T02 | 无交付包但当前COMPOSE已批准 | 默认可选COMPOSE；不进入镜头reviewInbox取源 | API |
| T03 | 最新COMPOSE未批准、旧COMPOSE已批准 | 默认不可超分；不偷偷回退旧源 | API/UI |
| T04 | 一个包有多个视频/无可确定主视频 | 要求明确来源或阻塞；不取first/max-size猜 | API |
| T05 | 选本页/跨页全选后修改筛选、刷新、切项目 | 精确ID集合、隐藏已选计数、新匹配项不自动加入、跨项目清空 | UI/E2E |
| T06 | 两次全选resolve期间新增成片或改批准 | 原selection/hash确定；修改后检查标stale，不扩大原集合 | API |
| T07 | F01/F02/F04，CONTAIN和COVER | 精确目标尺寸、正确定量留边/裁切、无意外拉伸；plan与产物一致 | Unit/FFmpeg |
| T08 | 2.25左右比例、原生倍率[2,3,4]或[4] | 选3或4；不向NCNN传2.25；超最大原生倍率明确阻塞 | Unit |
| T09 | 已1080p、无SR provenance、历史已SR | 默认跳过达标；是否AI处理由来源证据决定；显式新变体可重做 | API/UI |
| T10 | root已换、源包撤回、源hash不符 | 不可选/不可采用/不可新交付，历史结果保留可查 | API |
| T11 | 超上限201项、空选择、重复episode、跨项目ID | 422/409等稳定错误，零Job创建，无静默截断 | API |
| T12 | F09不支持格式 | 各自稳定错误码；不静默丢帧/转色/去隔行 | FFmpeg/API |

### 3.2 预设、模型、脚本配置

| ID | 输入/操作 | 必须结果 | 层 |
|---|---|---|---|
| T13 | 一键应用漫剧1080p | 填入设计默认、实际逐集target；生成production spec保持原值 | UI/API |
| T14 | Profile默认→preset→项目→batch→item | 值与来源链正确；locked不可覆盖；源UI显示差异 | Unit/API |
| T15 | 换模型，原参数不支持 | 明确不兼容字段；不把无效参数送CLI或静默忽略 | Unit/UI |
| T16 | NCNN选项面板 | 不展示FP32/denoise/face/prompt等假参数；tile0解释正确 | UI |
| T17 | 修改内置预设、保存用户版本、设项目默认 | 内置不覆盖，创建新版本；仅影响后续草稿，旧Job snapshot不变 | API/UI |
| T18 | 本批显式选Profile | 仅影响本批；项目/global assignment前后相同 | API |
| T19 | 安装程序缺失、模型hash不符、未发布、handler不存在 | 配置可见但不可执行；修复链接定位原因；不自动fallback | API/UI |
| T20 | 一键引擎配置+真实smoke | 必须显式确认；固定程序名与模型白名单；逐一验证声明倍率的两帧数量和像素；全部通过才写完整性、Offering、Profile证据并发布；任一失败零运行时登记 | GPU/E2E |
| T20a | 同一程序/权重/设备/参数重复一键配置 | 真实smoke可重跑，但复用同一不可变Profile；不重复发布、不产生冲突版本 | API |
| T20b | Profile A选择模型B或未验证倍率 | 计划阶段阻断，不能把不同模型或倍率送入NCNN | API |
| T21 | 自定义runner依赖被修改、路径含空格/中文 | 修改导致fingerprint失效；合法Unicode路径正常、无shell注入 | API/Windows |
| T22 | 向参数/文件名输入引号、`$()`、`&`、`..`、UNC/junction逃逸 | argv原样传递或字段拒绝；无额外命令执行、无越界读写 | Unit/Windows |
| T23 | NVENC不存在或不支持当前参数 | 默认libx264可用；硬件选项隐藏/阻塞；CRF和CQ不混用 | API/UI |

### 3.3 计划、批次、事务与幂等

| ID | 输入/操作 | 必须结果 | 层 |
|---|---|---|---|
| T24 | 请求50集预检 | HTTP快速202；CPU Job完成hash/probe；GET无新增Job | API |
| T25 | 一项阻塞，其余可执行 | 汇总准确；明确保留可执行项后新plan，原计划不静默跳项 | API/UI |
| T26 | submit前source/profile/preset/selection变化或到期 | 409 stale/expired；无新推理Job | API |
| T27 | 同Idempotency-Key双击/网络重发/并发提交 | 同batch和Job IDs，无重复GPU执行 | API/DB |
| T28 | 同key不同payload | 冲突，不返回误配旧batch | API |
| T29 | 创建第N个snapshot/Job/link时注入异常 | 整体rollback，无可领取孤儿Job、batch或悬空link | DB |
| T30 | 两个独立批次含同一fingerprint | 只执行一次，共享run；各item归属和进度正确 | DB/API |
| T31 | 取消共享run的一个批次item | 其他批仍有需求时Job继续；取消方状态独立 | API/E2E |
| T32 | 使用NEW_VARIANT、retry、PREVIEW三种操作 | 新变体新增run，retry同run新attempt，preview不能复用FULL结果 | Unit/API |
| T33 | API重启/浏览器storage清空后打开batch | 状态从DB恢复；没有前端重发全部任务 | API/E2E |
| T34 | 聚合缓存损坏/丢失、Job已完成但回写异常 | reconciler从links/Jobs/receipt修复，不重跑成功推理 | DB |

### 3.4 执行、队列、恢复

| ID | 输入/操作 | 必须结果 | 层 |
|---|---|---|---|
| T35 | 两个不同集同时排队 | 按优先级和序号执行，同GPU同时最多1个推理进程 | Worker/GPU |
| T36 | Comfy/Ollama等持有GPU，NCNN入队 | 使用同物理lease等待或正常切换；不并发抢显存、不杀外部任务 | Worker/GPU |
| T37 | Vulkan/CUDA设备顺序不同 | 使用验证映射；未验证设备不可开跑 | Unit/GPU |
| T38 | 首块推理失败/OOM | 有限次冻结tile回退并记录；耗尽后可恢复失败，不降级scale | Worker/GPU |
| T39 | 暂停待处理项 | 运行项继续，未领取项不启动；恢复后继续 | API/E2E |
| T40 | 暂停全部、运行时取消、立刻resume | 停止过程显示清楚，旧进程退出并settle前新attempt不可领取 | Worker/Windows |
| T41 | Worker被杀/机器重启 | lease过期可恢复，旧attempt无权登记；完整chunk复用 | Fault/Windows |
| T42 | checkpoint文件坏/少一块/模型版本变化 | 只复用有效块；模型/runner不一致禁止接续旧块 | Worker |
| T43 | decode少帧、NCNN漏输出/坏PNG、乱序文件名 | 整块失败，最终零正式登记；绝不吞帧 | Worker/FFmpeg |
| T44 | 分块首尾与非关键帧起点，24000/1001fps | 每帧只出现一次，总帧数和PTS正确，无块边界花屏/黑帧 | FFmpeg/GPU |
| T45 | 运行中磁盘不足 | 停止当前块并保留可恢复段，NEEDS_ATTENTION；释放后恢复 | Fault |
| T46 | work/project同卷、两个批次同时检查 | 合并预算与运行前重验，不双重低估峰值 | Unit/API |
| T47 | 输出rename完成后、DB登记前崩溃 | 按run receipt完成唯一登记，原文件未覆盖 | Fault/DB |
| T48 | DB已登记、Job完成前崩溃 | 恢复引用既有output；无重复render和交付包 | Fault/DB |
| T49 | 成功/失败/暂停目录清理、活跃lease、junction | 仅清理明确可回收run文件；不删有效checkpoint和源文件 | Windows |
| T50 | 每秒progress、进程stall、阶段未知总量 | 阶段/帧数真实，节流写入；未知值不伪造百分比；stall稳定报错 | Unit/UI |

### 3.5 音视频质量、审核与交付

| ID | 输入/操作 | 必须结果 | 层 |
|---|---|---|---|
| T51 | F03长片、首中尾同步标记 | 无累计漂移；原帧率有理数、帧数完整、尾帧/尾音不丢 | FFmpeg/GPU |
| T52 | F06音轨组合 | 保留音轨数量/语言/default/声道，兼容copy；转码有事实；无音频仍成功 | FFmpeg |
| T53 | F07字幕/水印、目标相同或变化 | 相同效果不重复；去除/变化冲突阻塞并建议COMPOSE源 | API/E2E |
| T54 | SDR range/matrix不同、缺色彩元数据 | 转换/保留正确；推断有标记；不只改tag造成明显色差 | FFmpeg/人工 |
| T55 | 成功生成第二、第三个SR候选 | `renders.latest`兼容字段仍指最新COMPOSE；采用版本不被新候选改变 | API/E2E |
| T56 | 新结果仅QC PASS、无人工APPROVED | 不允许采用/正式打包；审核检查不得自动勾PASS | API/UI |
| T57 | 批量审核含stale/缺检查、批量采用含一个未批准 | 整体无部分写入，逐项阻塞清楚；修复后原子成功；自动化已覆盖审核与采用两段 | API |
| T58 | 批量采用时selection revision被另一操作改变 | 409，无后写覆盖；UI刷新显示当前事实 | DB/API |
| T59 | root COMPOSE更新/approval拒绝/包撤回 | 派生stale，不能新打包；已存在包/历史审批不删除 | API |
| T60 | 当前生成480p，target480p，SR1080p | 不改生成规格；提供派生1080p target；原target历史不变 | API/UI |
| T61 | 采用横竖混合输出，批量打包 | 每集匹配各自目标档位；不错误拉伸横竖，不缩回480p | API/FFmpeg |
| T62 | 正式package产生 | 实际文件probe满足目标、manifest引用真实SR和source链；verify通过 | API/GPU |
| T63 | 其中一个DELIVERY_BUILD失败后重试 | 成功集不重建，失败集继续；目录/manifest幂等且不覆盖旧包 | Fault/API |
| T64 | 回到原版、创建新超分候选、重进单集页 | 选择恢复准确，target匹配校验有效，单集/项目页事实一致 | E2E |
| T65 | 检查对比播放器，试跑5秒 | 同步seek、单侧声音、100%裁切；预览代理标签明确；样片不可交付 | UI/E2E |
| T66 | 无高清ground truth | 不出现“PSNR证明质量提升”等伪结论；机器/人工状态明确 | UI/人工 |

### 3.6 兼容与规模

| ID | 输入/操作 | 必须结果 | 层 |
|---|---|---|---|
| T67 | 从旧DB升级、原有render/package/review | 原记录COMPOSE、历史可读取/验证，无多余最新selection自动写入 | Migration |
| T68 | 原单视频增强、整集合成、字幕、音频、正式交付 | 原功能成功，旧API/manifest兼容，SR候选不污染compose cache | Regression |
| T69 | 原所有Model Platform handler | 新execution context和submit改造向后兼容，Comfy等原流程不退化 | Regression |
| T70 | router/侧栏/面包屑/单集回链，刷新深链 | PROJECT/EPISODE scope正确；无跨项目资料泄漏 | UI/API |
| T71 | 50/200集列表，多history | 有界SQL和分页；不每集调用完整Director/Timeline服务 | Perf |
| T72 | 1440×900、1280×800、1024×768 | 设置/表格/主操作可见，无页面横滚、遮挡或不可达控件 | Browser |
| T73 | 键盘全程选择/配置/提交，屏幕阅读器状态 | checkbox半选、focus、label、loading/错误可读；不靠颜色区分 | Browser |
| T74 | 多标签/断网/恢复/全新浏览器打开队列 | 无重复提交，后台继续，恢复后读取服务端真实状态 | E2E |
| T75 | 应用安装/便携包运行，非repo cwd | 可定位已发布runner和依赖；缺安装明确报错 | Windows/UAT |
| T76 | V6成片已按source_audio_policy静音模型原声并混入正式TTS | 超分保留最终文件音轨，不重新读镜头模型原声、不重复混音；继承subtitle_burned_in事实 | API/FFmpeg |
| T77 | 480p画布内大面积稳定黑边、全黑转场、正常暗部 | 可解释的边框提醒及预览；不自动裁切、全黑/证据不足不假报确定区域 | Unit/UI |
| T78 | 自定义目标与源横竖相反，项目生产策略分别开/关 | 本批独立确认；默认阻塞跨向，无论项目生成开关如何；不修改原生产策略 | Unit/API/UI |

## 4. 真实输出判定

### 4.1 技术阈值（首发验收标准）

这些阈值是工程验收目标，实施中若因特定合法容器需要调整，要记录依据和对应fixture，不可把失败阈值临时放宽到测试通过。

| 项目 | 必须判定 |
|---|---|
| 输出像素 | width/height精确等于plan；SAR1:1、rotation0 |
| 帧数 | input decoded frames = sum chunk frame_count = output decoded frames；不允许丢1帧 |
| 帧率 | 保留有理数；按timebase允许的量化误差检验PTS，不将29.97替代30000/1001 |
| 视频时长 | 输出与按原帧时间线计算的差值≤1帧；末帧存在 |
| 声音同步 | 输出相对源的首/中/尾A/V标记变化≤max(1帧,20ms)；整体不能出现累计漂移 |
| 声音时长 | copy时解码样本与原有效音频相等；AAC重编码允许codec delay/padding但补偿后有效范围差≤max(1帧,50ms) |
| 音轨元数据 | 数量、language、default、声道映射符合冻结规则；无法保留时预检已说明 |
| 完整decode | ffmpeg完整解码退出成功且无损坏帧；头尾都检查 |
| 字幕sidecar | 继承原内容hash/时间戳或冻结revision重建hash；无未经说明的重写 |
| 源完整性 | 运行前后source hash一致；输入路径和历史包文件不变 |
| AI执行证据 | 模型文件hash、NCNN执行回执与真实中间输出，不能只凭1080p尺寸 |
| 最终包 | verify API通过；主视频实际规格与manifest/target一致；新版本审批和采用记录完整 |

### 4.2 人工画质检查

至少取真实漫剧中三段，每段5–10秒：人脸/细线/字幕，动作/镜头切换，暗部/渐变。比较原片按同输出尺寸插值、标准动漫模型和一个可选候选；固定同一时间点和裁切区域。

检查文字笔画破坏、眼睛/五官形变、轮廓光晕、纹理闪烁、过度平滑、块边界、不自然锐化和颜色变化。按PASS/NEEDS_CHANGES记录具体帧/时间码与理由。结果“更锐”不自动等于更好；某模型风格不合适允许选择其他已验证模型，记录最终采用依据。

模型smoke证明可运行，不保证整集画质。不得用一张静态封面代替运动样片检查。

## 5. 故障注入与恢复矩阵

| 断点 | 注入 | 可恢复资产 | 预期结果 |
|---|---|---|---|
| GPU领取前 | API/Worker停止 | 全部DB任务 | 重启后队列继续，无重复创建 |
| NCNN运行中 | 终止owned child | 已验证旧chunks | 当前块重算，旧进程资源释放 |
| checkpoint写入中 | 截断tmp/杀进程 | 先前committed chunks | 半写checkpoint不被复用 |
| 旧attempt继续回报 | 模拟lease超时及晚到receipt | 新attempt facts | fencing拒绝旧登记 |
| concat后/mux前 | 终止 | 验证segments | 重做mux，不重跑全部推理 |
| final rename后/DB前 | 终止 | final + receipt | 对账后唯一render登记 |
| render登记后/job完成前 | 终止 | render + receipt | 修复Job成功状态，不新增render |
| disk满 | 限额/故障adapter，不填满用户硬盘 | committed chunks | NEEDS_ATTENTION，释放后恢复 |
| 模型文件变化 | 测试副本改hash | 旧源与旧chunks | 阻塞；显式新版本重跑才改变参数 |
| 交付中 | 第二集copy/mux失败 | 第一集成功包 | 只重试第二集，新目录唯一 |

Windows进程树验收必须证明取消后没有孤立realesrgan/ffmpeg/runner进程继续使用GPU。不要只观察UI变成CANCELLED。

## 6. 性能与容量验收

参考目标环境：本机Windows、SQLite、现有React页面。报告明确CPU/GPU/显存/驱动/磁盘/引擎版本；未实测前不承诺分钟数。

- 200集聚合数据，50行一页：warm read p95≤500ms作为目标；最多6条有界SELECT，无按集N+1服务调用。若机器更慢，记录原始分布和瓶颈，不伪称达标。
- 提交预检/批次API目标≤2秒返回202，不在HTTP中跑长片probe全扫描或模型推理。批次最终commit事务目标≤1秒，基准采用50项；200项额外记录锁等待。
- GPU长任务期间健康检查和普通页面请求仍可响应，不能出现整个API事件循环被阻塞。
- 活跃批次2秒一次聚合请求；无逐行独立高频polling。离开页/终态停止无用请求。
- 默认分块下暂存PNG峰值与chunk size相关，而非与整集时长线性增长；segments和final占用分别计量。用1分钟与10分钟测试比较。
- GPU吞吐只报告实测；不把NCNN线程数提升当作可安全多集并行的证据。
- 同一快照恢复运行应明显复用完整chunks：记录复用帧数和新增推理帧数，证明没有全片重算。
- 清理任务只处理过保留期、无active lease的run；生成清理前后目录清单与释放字节数。

## 7. 建议测试文件与命令

### 7.1 文件组织

新增建议：`apps/api/tests/video_upscale/` 下按geometry、profiles、plans、batches、dedupe、source_resolver、runner、resume、qc、delivery、migration分文件。前端测试随`features/video-upscale`组件放置。浏览器新增`tests/e2e/episode_delivery_upscale.spec.ts`与真实验收`episode_delivery_upscale_windows_uat.spec.ts`。

真实GPU测试加 `video_upscale_gpu` marker，运行前验证配置与显式测试环境；默认safe suite排除它。修改pytest marker登记以及`scripts/test_api_safe.ps1`，保持既有 `not comfyui`，新增 `and not video_upscale_gpu`。缺模型时真实测试应SKIP并说明缺什么，不得把SKIP统计为通过；最终真实验收仍待完成。

测试不要为了省工直接用生产localhost端口和真实DB。fixture启动独立API/Worker，shell helper隐藏窗口；终止时只清理本次拥有的进程和目录。

### 7.2 实施后执行

下面命令在仓库根目录PowerShell执行，测试文件/脚本由后续实施创建。先定向测试，再项目必需check；不要在本次纯设计阶段执行或声称结果。

```powershell
& ./.venv/Scripts/python.exe -m pytest apps/api/tests/test_ncnn_video_upscale_profiles.py apps/api/tests/test_video_upscale_api.py apps/api/tests/test_video_upscale_execution.py apps/api/tests/test_video_upscale_geometry.py apps/api/tests/test_video_upscale_qc.py -m "not video_upscale_gpu"
pnpm --dir apps/web test -- --run src/pages/ProjectDeliveryPage.test.tsx src/features/production-settings-v2/VideoUpscaleDefaultsPanel.test.tsx src/pages/SystemPages.test.tsx
& ./.venv/Scripts/python.exe scripts/generate_client.py
pnpm --dir apps/web build
```

```powershell
pnpm exec playwright test -c tests/e2e/playwright.config.ts tests/e2e/episode_delivery_upscale.spec.ts
& ./.venv/Scripts/python.exe scripts/video_upscale/smoke.py --executable <realesrgan-ncnn-vulkan.exe> --model-dir <model-dir> --model-name realesr-animevideov3 --scale 3 --gpu-device 0 --tile 0 --output <evidence-json>
& ./.venv/Scripts/python.exe scripts/video_upscale/windows_uat.py prepare --config <isolated-config-json>
# 真人逐个查看 uat-session.json 中列出的全部视频后：
& ./.venv/Scripts/python.exe scripts/video_upscale/windows_uat.py finalize --session <uat-session-json> --reviewer <actual-reviewer> --confirm I_REVIEWED_EVERY_OUTPUT
```

尖括号内容为需替换的占位，不是可直接执行的路径。`smoke.py`只做独立诊断，不发布Profile；正式配置应在“能力与模型 / 视频超分引擎”执行一键真实验证与发布。`windows_uat.py`已实现且强制使用带所有权标记的全新绝对隔离目录；`prepare` 不会代替人工审核，`finalize` 必须由实际看完全部输出的人显式执行。其 2 秒合成横竖 fixture 证明真实引擎/媒体/交付闭环，不替代真实漫剧片画质验收，也不覆盖 UAT-B/C。若现有Playwright config自动连接共享实例，新增独立UAT config并使用它，不把上例命令作为覆盖现有环境的许可。

集成后运行仓库要求的 `scripts/check.ps1`（遵循更新后的安全测试过滤），覆盖类型、lint、maintainability、client/OpenAPI、生产构建和Web测试。真实模型测试单独执行并单独记证据，避免默认CI偷偷占用GPU。

已有回归重点：`test_g8_timeline_delivery.py`、`test_production_spec_timeline.py`、所有episode_render approval与Model Platform execution tests、`DeliveryPage/DeliveryWorkflowPanel/PostProcessPanel`、`routeRegistry/router/AppShell`、`JobsPage`及GPU lease测试。文件名称按实施时实际目录确认。

## 8. 三个端到端验收脚本

### UAT-A：正常整剧超分与交付

1. 隔离项目建立两集完整合法成片：横屏480p、竖屏480p，记录源hash与原生产规格。
2. 进入能力与模型配置真实NCNN引擎，验证并发布动漫Profile。
3. 从项目首页进入整剧交付，看到两集实测尺寸，多选。
4. 应用漫剧1080p，确认横竖不同target和原fps保留；保存用户预设并设项目默认。
5. 对第一集试跑5秒，对比成功；确认样片没有进入正式成片选择。
6. 检查两集、提交一次批次、再模拟网络重发一次，Job数量不增加。
7. 离开页面再打开，看到一个运行、一个等待；队列从服务端恢复。
8. 全部结束，核对输出技术QC、音轨、输入hash与模型证据。
9. 逐集人工审核，再批量采用；新建一个额外候选，确认已采用版本不改变。
10. 派生匹配的1080p交付目标，批量打包，验证实际横竖尺寸和manifest。
11. 回到单集交付页，显示同一采用版本和对应包；项目生成规格仍为480p。

### UAT-B：暂停、失败与恢复

1. 建立三集批次；第二集在指定chunk故障，第一集正常。
2. 第一集运行期间“暂停待处理项”，确认第一集继续，其余不启动；恢复。
3. 当前运行项“暂停全部”，确认child退出、GPU释放、完整chunks保留；立即恢复不产生并发旧attempt。
4. 重启Worker/API，恢复后只重算未完成chunk。
5. 第二集故障后点击重试失败项，第一集不再计算；恢复第三集。
6. 记录同run多attempt、复用帧数、最终唯一render及取消状态。

### UAT-C：历史交付文件与版本失效

1. 选择已确认且含水印、字幕的480p交付文件为源。
2. 超分后按同一效果目标打包，确认字幕/水印没有重复。
3. 改为要求去水印的目标，系统明确冲突；切干净COMPOSE源新建计划。
4. 源集生成新的COMPOSE，旧SR结果显示来源过期，不能再采用/新打包。
5. 已存在原包和SR包仍可验证/追溯，内容未被覆盖。

## 9. 交付门禁与证据模板

| Gate | 完成条件 |
|---|---|
| G1 合同与迁移 | schema、生成client、迁移升级、旧查询兼容、来源与选择语义测试通过 |
| G2 执行与恢复 | 真实NCNN样片 + FFmpeg媒体检查 + 故障/lease/暂停/重试通过 |
| G3 页面 | 正常/空/阻塞/失败/运行/完成，三视口和键盘关键路径通过 |
| G4 正式交付 | UAT-A/B/C中首发必备场景完整通过，manifest与真实文件一致 |
| G5 项目集成 | 必需check通过，操作说明、配置schema、脚本、证据、已知限制完整 |

只有G1–G5均满足，才可标`IMPLEMENTED_AND_VERIFIED`。真实环境缺失时可写`CODE_COMPLETE_REAL_RUNTIME_PENDING`，但不能对用户说该功能已全部验收。模拟通过、真实SKIP和人工未审分别统计。

证据建议放 `docs/evidence/video-upscale/<run-date>/`，包括：

- `summary.md`：commit、工作区差异、环境、执行命令、通过/失败/跳过、限制。
- `runtime-manifest.json`：程序/权重/runner/FFmpeg版本和hash、实际GPU及映射。
- `api-results.json`：project/batch/run/job/render/package IDs、计划与输出hash、审批采用链接。
- `media-qc.json`：原/输出probe、frame count、PTS、音轨、同步、完整decode结果。
- `recovery.json`：故障注入点、attempt/lease、复用块数、恢复后唯一登记事实。
- `performance.json`：样片区间、实际时长、峰值空间/显存、接口p50/p95、请求/SQL数量。
- 三视口截图与样片对比截帧，原始证据保留本机；展示给用户用缩略图。

每项Txx绑定具体test name或证据路径；不能仅写“已测试全部功能”。发现不属于本功能的既有问题，单独记录baseline，说明是否影响本次闭环，不用其掩盖新回归。
