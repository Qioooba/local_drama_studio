# OpenAPI artifacts

`openapi.json` 和 `apps/web/src/generated/api.ts` 由 `python scripts/generate_client.py` 从 FastAPI app factory 生成。提交前必须重新生成并运行前端类型检查；不能手改生成文件冒充契约同步。

