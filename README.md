# LocalDramaStudio

本目录是 `LocalDramaStudio_Blueprint_v2` 的实现仓库，和工作区中既有的旧脚本、旧项目及样片隔离。

首版边界：

- 默认 `LOCAL_ONLY`；只绑定 `127.0.0.1`/`localhost`/`[::1]`。
- 不实现云 Provider、API Key、计费、多租户或 G11 legacy 迁移。
- H3 只能以本机 `model_manifest.json` 证据导入为候选 Profile；未经 runtime smoke/regression 不得激活。
- 生产状态以 SQLite/WAL 为权威；媒体内容以项目文件系统为权威；版本和审核记录不可覆盖。

实施严格遵循 G0→G10。阶段报告、ADR、风险、追踪和 release evidence 位于 `docs/`。

## 部署模式

稳定版部署已统一由原生 Runtime Host 管理，不再把 `scripts/start.ps1` 当作生产生命周期实现。Windows 安装包/portable 与 Linux systemd 包共享同一配置、数据库迁移、恢复集、签名清单和升级状态机：

- [Windows 部署](docs/deployment/windows.md)
- [Linux 部署](docs/deployment/linux.md)
- [升级与恢复手册](docs/operations/upgrade-recovery-runbook.md)
- [构建发行包](packaging/README.md)
- [完整架构设计](docs/plan/windows-first-cross-platform-runtime-release-architecture-2026-08-26.md)

通过 `LOCAL_DRAMA_NETWORK_MODE` 切换：

| 模式 | 行为 |
|---|---|
| `LOCAL_ONLY`（默认） | 单机桌面形态；API 仅绑定 loopback；行为与首版完全一致。 |
| `LAN_SERVICE` | Windows 服务器形态；可绑定局域网地址（`LOCAL_DRAMA_HOST=0.0.0.0`），API 直接托管前端（`apps/web/dist`，单端口同源）；局域网机器的浏览器可直接访问页面、上传文件/剧本、实时预览视频。ComfyUI 等运行时 endpoint 允许指向 RFC1918 私网地址（仍禁止公网）。 |

完整环境变量清单与部署步骤见 `docs/plan/server-deployment-refactor-2026-08-25.md`。

> 安全提醒：`LAN_SERVICE` 沿用单用户信任模型（无登录鉴权）。请用防火墙把服务端口限制在可信网段。

Windows 正式安装包默认选择“Trusted LAN server”，会原子合并 Server profile、安装延迟自动启动服务，并创建仅允许 Windows `LocalSubnet` 的 TCP 端口规则；安装时可取消该选项，切换为仅本机 Desktop 模式。升级会保留用户的存储、运行时、端口和 Origin 配置；切回 Desktop 或卸载时会删除该防火墙规则和服务。`LAN_SERVICE` 必须显式配置 `trusted_lan_unauthenticated=true`，避免在没有确认信任边界时静默开放。

远程使用约定：Mac/其他电脑选择的剧本、媒体、LUT、授权证据和项目包均由浏览器上传到 Windows 服务端；导出物通过浏览器下载。模型权重保留在服务端，由 `LOCAL_DRAMA_MODEL_LIBRARY_ROOTS` 指定受控模型库，远程浏览器不会访问任意 Windows 路径，也不会触发服务器桌面的文件选择窗口。

