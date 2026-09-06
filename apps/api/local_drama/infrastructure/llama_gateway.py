"""Always-on OpenAI-compatible gateway for the managed llama.cpp runtime.

The gateway owns the public/LAN port but no CUDA memory.  A generation request
acquires the repository-wide GPU lease, starts (or adopts) llama-server on its
loopback-only internal port, and proxies the response.  The lease is released
after the request while the model remains warm; an idle watchdog evicts it.
Any other project GPU runtime can therefore acquire the lease and evict the
managed llama-server immediately through the shared PID-file contract.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager, suppress
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from local_drama.application.gpu_runtime import GpuRuntimeCoordinator
from local_drama.application.job_resources import GpuRuntime
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.llama_server_manager import LlamaServerManager

_HOP_HEADERS = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


class LlamaGateway:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.database_path)
        self.manager = LlamaServerManager(
            log_dir=settings.logs_root / "llama",
            startup_timeout_seconds=settings.llama_startup_timeout_seconds,
        )
        self.coordinator = GpuRuntimeCoordinator(self.database, settings, llama_manager=self.manager)
        self.request_lock = asyncio.Lock()
        self.last_activity = time.monotonic()
        self._idle_task: asyncio.Task[None] | None = None

    @property
    def internal_base_url(self) -> str:
        host = "127.0.0.1" if self.settings.llama_server_host in {"0.0.0.0", "::", "*"} else self.settings.llama_server_host
        return f"http://{host}:{self.settings.llama_server_port}"

    async def start(self) -> None:
        self._idle_task = asyncio.create_task(self._idle_watchdog(), name="llama-gateway-idle")

    async def close(self) -> None:
        if self._idle_task is not None:
            self._idle_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._idle_task
        # Always release the managed llama-server child process when the
        # gateway shuts down. Without this, a gateway crash / OOM / SIGKILL
        # leaves the ~20 GB GGUF in VRAM and the loopback port bound, so the
        # next manager that starts will either double-load the model or hit
        # the "port in use" orphan-recovery window.
        try:
            await asyncio.to_thread(self.manager.stop)
        except Exception:
            # Closing must never raise; the supervisor / lifespan handler
            # would otherwise leave an orphan anyway.
            pass

    def public_models(self) -> dict[str, object]:
        model = self.settings.llm_model or (self.settings.llama_model_path.stem if self.settings.llama_model_path else "managed-llama")
        capabilities = ["completion", "multimodal"] if any(arg in {"--mmproj", "-mm"} for arg in self.settings.llama_server_args) else ["completion"]
        item = {
            "id": model,
            "name": model,
            "model": model,
            "object": "model",
            "owned_by": "local-drama-llama-gateway",
            "capabilities": capabilities,
            "meta": {"n_ctx": self.settings.llama_ctx_size},
        }
        return {"object": "list", "data": [item], "models": [item]}

    def status(self) -> dict[str, object]:
        running = self.manager.is_running() or self.manager.has_owned_process_record()
        return {
            "status": "ok",
            "gateway": True,
            "model_loaded": running,
            "idle_timeout_seconds": self.settings.llama_idle_timeout_seconds,
            "internal_endpoint": self.internal_base_url,
        }

    async def proxy(self, request: Request, path: str) -> Response:
        async with self.request_lock:
            lease, borrowed = await self._acquire_wait()
            heartbeat_stop = asyncio.Event()
            heartbeat = None if borrowed else asyncio.create_task(self._heartbeat(lease["token"], heartbeat_stop))
            succeeded = False
            try:
                if not borrowed:
                    await asyncio.to_thread(
                        self.coordinator.prepare,
                        GpuRuntime.LLAMA_CPP,
                        owner_ref=lease["owner_ref"],
                        activation_context=None,
                    )
                body = await request.body()
                headers = {key: value for key, value in request.headers.items() if key.casefold() not in _HOP_HEADERS}
                url = f"{self.internal_base_url}/{path}"
                if request.url.query:
                    url += f"?{request.url.query}"
                async with httpx.AsyncClient(timeout=httpx.Timeout(3600.0)) as client:
                    upstream = await client.request(request.method, url, content=body, headers=headers)
                response_headers = {
                    key: value for key, value in upstream.headers.items() if key.casefold() not in _HOP_HEADERS
                }
                succeeded = True
                self.last_activity = time.monotonic()
                return Response(
                    content=upstream.content,
                    status_code=upstream.status_code,
                    headers=response_headers,
                    media_type=upstream.headers.get("content-type"),
                )
            except DomainRuleError as error:
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": {
                            "code": error.code,
                            "message": error.message,
                            "type": "gpu_runtime_unavailable",
                            "retryable": True,
                        }
                    },
                )
            except httpx.HTTPError as error:
                return JSONResponse(
                    status_code=502,
                    content={"error": {"code": "LLAMA_GATEWAY_UPSTREAM_FAILED", "message": str(error), "type": "upstream_error"}},
                )
            finally:
                heartbeat_stop.set()
                if heartbeat is not None:
                    with suppress(asyncio.CancelledError):
                        await heartbeat
                if not borrowed:
                    await asyncio.to_thread(
                        self.coordinator.release,
                        lease["token"],
                        reason="COMPLETED" if succeeded else "FAILED",
                    )

    async def _acquire_wait(self) -> tuple[dict[str, str], bool]:
        deadline = time.monotonic() + 600.0
        owner_ref = f"llama-gateway:{uuid.uuid4()}"
        while True:
            try:
                lease = await asyncio.to_thread(
                    self.coordinator.acquire,
                    GpuRuntime.LLAMA_CPP,
                    owner_kind="LLAMA_GATEWAY",
                    owner_ref=owner_ref,
                )
                return lease, False
            except DomainRuleError as error:
                if error.code != "GPU_RUNTIME_BUSY" or time.monotonic() >= deadline:
                    raise
                # Project workers already hold the same LLAMA_CPP lease before
                # calling their configured OpenAI endpoint. Borrow that lease
                # instead of deadlocking on a nested acquisition; the worker
                # remains responsible for heartbeat and cleanup.
                status = await asyncio.to_thread(self.coordinator.status)
                active = status.get("active_lease")
                if isinstance(active, dict) and active.get("runtime_kind") == GpuRuntime.LLAMA_CPP.value:
                    return {"token": "", "runtime": GpuRuntime.LLAMA_CPP.value, "owner_ref": str(active.get("owner_ref") or "")}, True
                await asyncio.sleep(1.0)

    async def _heartbeat(self, token: str, stop: asyncio.Event) -> None:
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.coordinator.HEARTBEAT_SECONDS)
                return
            except TimeoutError:
                await asyncio.to_thread(self.coordinator.heartbeat, token)

    async def _idle_watchdog(self) -> None:
        interval = max(2.0, min(10.0, self.settings.llama_idle_timeout_seconds / 4))
        while True:
            await asyncio.sleep(interval)
            if self.request_lock.locked() or time.monotonic() - self.last_activity < self.settings.llama_idle_timeout_seconds:
                continue
            async with self.request_lock:
                if time.monotonic() - self.last_activity < self.settings.llama_idle_timeout_seconds:
                    continue
                if not (self.manager.is_running() or self.manager.has_owned_process_record()):
                    continue
                try:
                    lease = await asyncio.to_thread(
                        self.coordinator.acquire,
                        GpuRuntime.LLAMA_CPP,
                        owner_kind="LLAMA_GATEWAY_IDLE",
                        owner_ref=f"llama-gateway-idle:{uuid.uuid4()}",
                    )
                except DomainRuleError as error:
                    if error.code == "GPU_RUNTIME_BUSY":
                        continue
                    raise
                try:
                    await asyncio.to_thread(
                        self.coordinator.cleanup,
                        GpuRuntime.LLAMA_CPP,
                        retain_if_same_runtime_waiting=False,
                    )
                finally:
                    await asyncio.to_thread(self.coordinator.release, lease["token"], reason="IDLE_TIMEOUT")


def create_llama_gateway_app(settings: Settings) -> FastAPI:
    gateway = LlamaGateway(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await gateway.start()
        try:
            yield
        finally:
            await gateway.close()

    app = FastAPI(title="LocalDramaStudio llama.cpp Gateway", lifespan=lifespan)
    app.state.gateway = gateway
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    @app.get("/v1/health")
    async def health() -> dict[str, object]:
        return gateway.status()

    @app.get("/models")
    @app.get("/v1/models")
    async def models() -> dict[str, object]:
        return gateway.public_models()

    @app.get("/props")
    async def props() -> dict[str, object]:
        return {**gateway.status(), "is_sleeping": not bool(gateway.status()["model_loaded"])}

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def proxy(path: str, request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204)
        return await gateway.proxy(request, path)

    return app
