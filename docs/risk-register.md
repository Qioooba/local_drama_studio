# 风险登记册（G0）

| ID | 风险 | 触发条件 | 控制/证据 | 门禁 |
|---|---|---|---|---|
| RISK-001 | H3/Comfy Windows 进程或显存不稳定 | 残留、崩溃、OOM | ephemeral worker、GPU 独占 1、reconciliation、显存回收测试 | G6/G10 |
| RISK-002 | 通用系统被旧项目目录反向绑定 | 出现 CUT/固定旧路径硬编码 | 标准 v2 领域与 package；legacy 仅 G11 | G0+ |
| RISK-003 | SQLite 写竞争/长事务 | busy、锁等待、API p95 超标 | WAL、短事务、read model、性能 fixture | G2/G5/G10 |
| RISK-004 | 大媒体使 UI/内存崩溃 | 全片加载或 N+1 请求 | Range、proxy、virtual list、lazy cache | G3/G9/G10 |
| RISK-005 | loopback 响应歧义重复提交 | 超时但 provider 已接收 | provider_job_id、history/reconcile、NEEDS_ATTENTION | G5/G7 |
| RISK-006 | 插件/错误 URL 公网出站 | 任意非 loopback 请求 | LOCAL_ONLY 校验、网络拦截、stop-the-line | G7/G10 |
| RISK-007 | 自定义 Comfy 节点供应链风险 | lock/hash/依赖变更 | 独立环境、可信清单、发布评审 | G6/G10 |
| RISK-008 | 模型/workflow 变化导致不可复现 | hash/依赖变化 | snapshot、manifest、旧 Profile 回滚 | G6/G7 |
| RISK-009 | 阶段过大导致大爆炸实现 | 跨阶段修改或无证据交付 | 单阶段计划、阶段报告、门禁冻结 | G0+ |
| RISK-010 | 页面成为巨型组件 | 页面 >700 行或混合业务规则 | feature 拆分、read model、CI 行数警告 | G3+ |
| RISK-011 | 60 集/800+ shots/10k media 压力 | API/UI/队列延迟超标 | 规模 fixture、索引、分页、性能基线 | G10 |
| RISK-012 | 外部工具改动正式媒体 | hash/integrity mismatch | probe/hash、备份、阻塞 selection/approval/delivery | G3+ |
| RISK-013 | Timeline v1 失控为完整 NLE | 复杂特效/多机位进入主线 | OUT-002、白名单和 P2 评审 | G8 |
| RISK-014 | AI 内容质量无法自动验收 | 机器 QC 与审美不一致 | 技术 QC + 人工硬闸门，不自动批准 | G4+ |
| RISK-015 | 备份“成功”但不可恢复 | integrity/hash/路径恢复失败 | online backup、manifest、恢复到新根演练 | G2/G10 |
| R-VAR-001 | retry 被误作创作重抽 | take/成本/谱系失真 | retry/variant 分离 API、审计和 TC-VAR | G2/G5/G6 |
| R-VAR-002 | seed 被误宣称逐像素确定 | 不同 Runtime/硬件结果不同 | Profile determinism 声明、UI 明示 | G6/G7 |
| R-VAR-003 | 实验组合爆炸 | seed×prompt×image×profile 超阈值 | plan、cell cap、预算、二次确认、懒展开 | G5/G9/G10 |
| R-VAR-004 | 不支持尾帧被静默丢弃 | Profile contract 与 binding 不一致 | 双重 capability validation，阻塞提交 | G6/G7 |
| R-VAR-005 | 连续性 stale 不传播 | 上游边界/批准改变 | TransitionConstraint revision + reconciler | G4/G8/G10 |
| R-CAP-001 | 竞品参数变成系统承诺 | UI 写死第三方上限 | 仅 Profile 声明真实能力 | G7/G10 |
| R-CAP-002 | DOCX/压缩包解析攻击 | 宏、外链、zip bomb、畸形文件 | staging、MIME/大小、禁宏/外链、hash | G3/G10 |
| R-CAP-003 | 运镜控件未被模型消费 | UI 有假滑块 | NATIVE/PROMPT_FALLBACK/UNSUPPORTED 编译结果 | G6/G7 |
| R-CAP-004 | 后处理导致媒体/磁盘爆炸 | 多步 recipe 无预算 | plan、cache、hard stop、原生/增强规格分离 | G8/G10 |
| R-CAP-005 | webhook 触达公网或内网敏感服务 | 非 loopback target | loopback、解析后 IP 校验、scope、签名、审计 | G9/G10 |

