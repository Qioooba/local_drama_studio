# G1 阶段退出报告

- 基线：`local_drama_studio` G1 工程骨架；OpenAPI snapshot 位于 `docs/openapi/openapi.json`
- app version：`0.1.0-g1`
- schema version：业务 schema 尚未创建；G2 负责 Alembic 初始 migration
- 完成需求：G1-01—G1-08；对应 NFR-SEC-001、NFR-PRIV-001、NFR-OBS-001、NFR-COMP-001、NFR-MAINT-001 的工程骨架

## 代码与契约

- FastAPI app factory、LOCAL_ONLY config、request id、结构化错误模型、Origin 写请求边界和安全 headers。
- `/api/v1/health/live`、`/health/ready`、`/health/dependencies`、`/system/contract`。
- React 19 + TypeScript strict + Vite + TanStack Query app shell；页面真实调用 health/contract API，未用静态业务数据。
- OpenAPI 由 `scripts/generate_client.py` 生成，并生成 `apps/web/src/generated/api.ts`。
- PowerShell `start.ps1`/`stop.ps1`/`doctor.ps1`/`check.ps1`；stop 只按 tracked PID + command line 匹配。

## 测试证据

- `python scripts/g0_validate.py`：86 FR、14 NFR、85 TC，manifest/H3 约束输出。
- `python -m compileall -q apps/api scripts`：通过。
- `apps/api` pytest：3 passed，0 failed，1 warning（Starlette TestClient 对 httpx 的弃用提示，不影响功能；G2 前评估升级路径）。
- Ruff：通过。
- mypy strict：通过，8 个 Python 源文件。
- `pnpm --dir apps/web build`：TypeScript strict + Vite build 通过。
- `pnpm --dir apps/web test`：1 passed，0 failed。
- start/health/stop smoke：API PID 29588 启动、`/health/live` 200、按 command line 安全停止通过。

## 安全/恢复边界

- 默认 API 绑定 `127.0.0.1`；写请求带非白名单 Origin 返回稳定 `ORIGIN_NOT_ALLOWED`。
- 首版 contract 明确 `LOCAL_ONLY`、remote provider disabled、legacy migration deferred to G11。
- G1 没有业务媒体写入、数据库 migration 或后台 job，因此不声称完成 G2/G5 恢复能力。

## 已知问题与下一阶段输入

- `health/ready` 的 database 为 `not_configured_until_g2`；这是 G1 的明确边界，不是假 ready。
- Comfy Designer/Production、Profile、SQLite schema 尚未接入；G2 先实现领域和 migration。
- E2E Playwright 配置和测试已建立，浏览器安装/执行纳入 G3+门禁。

## 回滚

删除 `local_drama_studio` G1 新增代码/依赖即可；未修改工作区既有旧项目、模型、样片和旧脚本。运行时 PID 文件只在 smoke 时创建并已清理。

## 门禁结论

`PASS — G1 engineering skeleton is green; G2 may start.`

