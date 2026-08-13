# ADR-0013：LOCAL_ONLY 本地适配器统一契约

状态：Accepted（2026-08-14）

G7-03/G7-07 以静态 `AdapterContractRegistry` 统一描述 ComfyUI loopback、OpenAI-compatible loopback、Local CLI、FFmpeg/FFprobe 四类本地能力。契约检查只验证 transport、loopback 主机、凭据和本地 executable 引用，不打开 socket、不启动进程、不加载模型、不改变数据库。

`REMOTE_*` transport、公网 HTTP、带凭据的 loopback URL 和远程 executable 引用必须被拒绝。真实运行仍由既有客户端和 worker 执行，只有真实探针或执行证据才能改变能力状态；静态契约的 `runtime_contacted`、`network_contacted`、`mutated` 始终为 `false`。

证据：`GET /api/v1/adapters/contracts`、`apps/api/tests/test_adapter_contracts.py`。
