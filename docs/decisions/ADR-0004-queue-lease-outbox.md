# ADR-0004：SQLite 持久队列、lease 与 outbox

- 状态：Accepted for G0/G2/G5 implementation baseline
- 需求：FR-JOB-001、FR-JOB-004、NFR-REL-001、NFR-REL-002、NFR-OBS-001

## 决策

使用 SQLite WAL + 短事务作为单机队列权威。claim 在一个事务中选择可执行 job、创建/更新 attempt、写入不可猜 lease token 和过期时间。heartbeat 必须携带匹配 token；过期由 reconciler 分类为可安全重排队、需收集、FAILED 或 NEEDS_ATTENTION。业务事务同时写 transactional outbox，SSE 断线后由查询模型重建权威状态。

重试只创建同一 Job 的新 JobAttempt；创作重抽必须创建 GenerationVariant 和新 Job。输出先进入 sandbox，经 probe/hash/contract 后以幂等方式注册。

