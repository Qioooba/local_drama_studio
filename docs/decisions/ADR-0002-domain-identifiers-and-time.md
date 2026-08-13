# ADR-0002：标识、顺序、时间与帧率表达

- 状态：Accepted for G0/G2 implementation baseline
- 需求：FR-PRJ-004、FR-WRT-002、NFR-COMP-001

## 决策

- 持久化 ID 使用 UUID 字符串，代码/目录显示编号稳定且不承担身份。
- Shot 顺序使用可插入 `order_key`；导出时计算 display number，重排不改变 UUID、code 或历史引用。
- 数据库时间统一 UTC RFC3339；UI 默认按 Asia/Shanghai 展示。
- 视频时间用整数微秒/毫秒或明确 timebase；fps 使用 numerator/denominator，不使用浮点秒作为权威值。
- Windows 项目内部路径统一规范化 POSIX 相对路径，不能进入盘符、UNC、`..` 或符号链接逃逸。

