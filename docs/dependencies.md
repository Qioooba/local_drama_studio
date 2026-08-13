# G1 依赖与许可证清单

锁定文件：`apps/api/requirements.lock`、`pnpm-lock.yaml`。以下为直接依赖的许可证记录；发布前 G10 会由 SBOM 生成器重新核验完整传递依赖。

| 组件 | 用途 | 许可证 |
|---|---|---|
| FastAPI / Starlette / Uvicorn | 本机 API、ASGI | MIT / BSD-3-Clause |
| Pydantic | API schema/config | MIT |
| SQLAlchemy / Alembic | SQLite ORM/migration（G2 起） | MIT |
| HTTPX | loopback adapter/test client | BSD-3-Clause |
| pytest / pytest-asyncio | Python 测试 | MIT |
| Ruff / mypy | Python 质量门禁 | MIT |
| React / React DOM | 前端 UI | MIT |
| TanStack Query | server state（G1 shell） | MIT |
| Vite / TypeScript / Vitest | 构建、类型、前端测试 | MIT / Apache-2.0 / MIT |
| Playwright | E2E 浏览器测试 | Apache-2.0 |

未引入 Redis、Celery、PostgreSQL、Electron、远程 Provider SDK 或模型下载器。

