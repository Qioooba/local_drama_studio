# ADR-0002：Windows 首发、Linux 兼容的 Runtime Host 与版本化发布架构

- 状态：Accepted
- 日期：2026-08-26
- 详细设计：`docs/plan/windows-first-cross-platform-runtime-release-architecture-2026-08-26.md`
- 取代：以 `scripts/start.ps1` / `scripts/stop.ps1` 作为生产生命周期权威的路线

## 背景

当前源码版已经具备 FastAPI、React、SQLite/WAL、Alembic、durable Worker 和 LAN 服务能力，但代码、运行环境、数据、迁移与生命周期仍由仓库目录和 PowerShell 脚本隐式耦合。该结构无法提供稳定安装、进程守护、原子升级、失败回滚，也会把后续 Linux 部署锁死在 Windows 专属能力上。

## 决策

1. 保留现有模块化单体业务核心，不拆微服务，不重建 Job/媒体/审核真值。
2. 新增小型 Go Runtime Host，负责单实例、API/Worker 子进程、健康检查、OS 服务集成、版本选择、升级与回滚；Host 不包含业务逻辑。
3. Python 业务运行时增加正式 API/Worker/Maintenance entrypoint、ResourceLocator、版本化机器配置和 Platform Ports。
4. Windows SAPI、文件选择器、Credential Manager 和文件系统能力移入 Windows adapter；Linux 使用同一 Port 的 Linux adapter 或明确的 unavailable adapter。
5. 生产发行使用只读 versioned onedir，持久数据位于版本目录之外；生产机器不依赖系统 Python、Node 或 pnpm。
6. 正式启动不执行数据库 migration。升级必须先建立 Recovery Set、在副本演练、迁移正式库、启动候选版本并验证，失败时恢复备份和旧版本。
7. Windows 首发支持 DESKTOP 和 SERVER；Linux 首期支持 systemd SERVER。两者共享 Python wheel、Web dist、migration 和发布合同。

## 后果

- 新功能不得直接引入 OS 专属调用，必须先扩展 Platform Port。
- 旧 PowerShell 启停脚本降级为开发/兼容入口，最终删除。
- 增加 Go 构建工具链，但 Go 代码严格限制在部署控制平面。
- 安装、升级、回滚测试必须对真实安装产物执行，源码测试不能替代发布验证。
- 普通应用升级不能更新不兼容的 Host；Host protocol 变化要求完整安装器升级。

## 回滚

实施阶段可在新 Host 达到等价门禁前继续使用旧开发脚本，但不得为旧脚本增加新的生产能力。业务数据库不因本 ADR 重建；新运行时失败可继续由旧源码版指向原外置数据根。完成正式升级后，代码回滚必须与 Recovery Set 中匹配的数据库和配置共同进行，禁止 Alembic downgrade。
