"""Production declarations shared by V2 submission and Worker processes."""

from __future__ import annotations

from typing import Any, Callable

from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.comfy_workflow_execution import comfy_workflow_handler_identity, make_comfy_workflow_handler
from local_drama.model_platform.application.execution_handlers import ExecutionHandlerDescriptor, ExecutionHandlerRegistry
from local_drama.model_platform.application.llama_cpp_text_execution import llama_cpp_text_handler_identity, make_llama_cpp_text_handler
from local_drama.model_platform.application.ncnn_video_upscale_execution import make_ncnn_video_upscale_handler, ncnn_video_upscale_handler_identity
from local_drama.model_platform.application.ollama_text_execution import make_ollama_text_handler, ollama_text_handler_identity
from local_drama.model_platform.application.pytorch_embedding_execution import make_pytorch_embedding_handler, pytorch_embedding_handler_identity
from local_drama.model_platform.application.worker_execution_handlers import WorkerExecutionHandlerDescriptor, WorkerExecutionHandlerRegistry
from local_drama.model_platform.domain.capabilities import CANONICAL_CAPABILITIES

_TEXT_CAPABILITIES = frozenset({"LLM_STORY_PARSE", "LLM_EPISODE_PLAN", "LLM_STORYBOARD", "LLM_PROMPT_REWRITE"})


def production_execution_handlers() -> ExecutionHandlerRegistry:
    embedding_code, embedding_version = pytorch_embedding_handler_identity()
    comfy_code, comfy_version = comfy_workflow_handler_identity()
    ollama_code, ollama_version = ollama_text_handler_identity()
    llama_code, llama_version = llama_cpp_text_handler_identity()
    ncnn_code, ncnn_version = ncnn_video_upscale_handler_identity()
    return ExecutionHandlerRegistry([
        ExecutionHandlerDescriptor(embedding_code, embedding_version, "EMBEDDING_TEXT", frozenset({"pytorch.embedding.qwen3"}), "CPU", "PYTORCH"),
        *(ExecutionHandlerDescriptor(ollama_code, ollama_version, capability, frozenset({"ollama.chat.v1"}), "GPU_H3", "OLLAMA") for capability in sorted(_TEXT_CAPABILITIES)),
        *(ExecutionHandlerDescriptor(llama_code, llama_version, capability, frozenset({"llama.chat.v1"}), "GPU_H3", "LLAMA_CPP") for capability in sorted(_TEXT_CAPABILITIES)),
        # NCNN is a one-shot child process.  The existing PYTORCH lifecycle
        # class is the scheduler's generic process-owned GPU category: it
        # evicts resident daemons before launch and has nothing to retain.
        ExecutionHandlerDescriptor(ncnn_code, ncnn_version, "UPSCALE_VIDEO", frozenset({"ncnn.realesrgan.video.v1"}), "GPU_H3", "PYTORCH"),
        *(ExecutionHandlerDescriptor(comfy_code, comfy_version, capability, frozenset({"comfy.workflow.v1"}), "GPU_H3", "COMFY") for capability in sorted(CANONICAL_CAPABILITIES)),
    ])


def production_worker_execution_handlers(
    settings: Settings,
    database: Database | None = None,
    *,
    cancel_check: Callable[[], bool] | None = None,
    report_progress: Callable[[dict[str, Any]], bool] | None = None,
    attempt_context: Callable[[], tuple[str, str, str] | None] | None = None,
) -> WorkerExecutionHandlerRegistry:
    embedding_code, embedding_version = pytorch_embedding_handler_identity()
    comfy_code, comfy_version = comfy_workflow_handler_identity()
    ollama_code, ollama_version = ollama_text_handler_identity()
    llama_code, llama_version = llama_cpp_text_handler_identity()
    ncnn_code, ncnn_version = ncnn_video_upscale_handler_identity()
    descriptors = [
        WorkerExecutionHandlerDescriptor(embedding_code, embedding_version, "EMBEDDING_TEXT", frozenset({"pytorch.embedding.qwen3"}), make_pytorch_embedding_handler(settings)),
        WorkerExecutionHandlerDescriptor(ollama_code, ollama_version, "*", frozenset({"ollama.chat.v1"}), make_ollama_text_handler(settings)),
        WorkerExecutionHandlerDescriptor(llama_code, llama_version, "*", frozenset({"llama.chat.v1"}), make_llama_cpp_text_handler(settings)),
        WorkerExecutionHandlerDescriptor(comfy_code, comfy_version, "*", frozenset({"comfy.workflow.v1"}), make_comfy_workflow_handler(settings)),
    ]
    if database is not None:
        descriptors.append(
            WorkerExecutionHandlerDescriptor(
                ncnn_code,
                ncnn_version,
                "UPSCALE_VIDEO",
                frozenset({"ncnn.realesrgan.video.v1"}),
                make_ncnn_video_upscale_handler(
                    database,
                    settings,
                    cancel_check=cancel_check,
                    report_progress=report_progress,
                    attempt_context=attempt_context,
                ),
            )
        )
    return WorkerExecutionHandlerRegistry(descriptors)
