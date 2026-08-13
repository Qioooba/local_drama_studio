# ADR-0006：首版 LOCAL_ONLY 与零公网出站

- 状态：Accepted for G0 implementation baseline
- 需求：FR-PRV-002、FR-PRV-004、NFR-SEC-001、NFR-SEC-003、NFR-PRIV-001

## 决策

首版固定 `LOCAL_ONLY`。Runtime base URL 仅接受 `localhost`、`127.0.0.1`、`[::1]`；本地失败只进入失败/阻塞，不 fallback 到远端。远程 Provider、API key、计费、费用和调用页面不进入首版 OpenAPI/UI。未来 transport 只能实现同一 adapter contract，并必须另行进行 secret/egress/数据保留评审。

## 验证

G7/G10 使用网络记录器/阻断器覆盖完整本地链路，公网请求数必须为 0；任何越界访问触发安全事件并 stop-the-line。

