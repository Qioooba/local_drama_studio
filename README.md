# LocalDramaStudio

本目录是 `LocalDramaStudio_Blueprint_v2` 的实现仓库，和工作区中既有的旧脚本、旧项目及样片隔离。

首版边界：

- LOCAL_ONLY；默认只绑定 `127.0.0.1`/`localhost`/`[::1]`。
- 不实现云 Provider、API Key、计费、多租户或 G11 legacy 迁移。
- H3 只能以本机 `model_manifest.json` 证据导入为候选 Profile；未经 runtime smoke/regression 不得激活。
- 生产状态以 SQLite/WAL 为权威；媒体内容以项目文件系统为权威；版本和审核记录不可覆盖。

实施严格遵循 G0→G10。阶段报告、ADR、风险、追踪和 release evidence 位于 `docs/`。

