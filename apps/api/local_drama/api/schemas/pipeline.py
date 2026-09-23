from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

ALLOWED_VISUAL_STYLES = {
    "国风仙侠 电影级写实 (Cinematic Realistic)",
    "现代都市 悬疑写实 (Urban Suspense)",
    "玄幻奇幻 动漫风格 (Anime Fantasy)",
    "复古港风 胶片质感 (Vintage Film)",
    "科幻赛博 霓虹写实 (Cyberpunk Sci-Fi)",
}


class LLMConfig(BaseModel):
    provider: str = Field(default="LLAMA_CPP_MANAGED", description="LLAMA_CPP_MANAGED, OPENAI_COMPAT, or legacy OLLAMA_LOOPBACK")
    base_url: str = Field(default="http://127.0.0.1:28088", description="Base URL of the LLM endpoint")
    model: str = Field(default="Qwen3.8-27B-UD-Q4_K_M", description="Model name or managed llama.cpp alias")
    api_key: str | None = Field(default=None, description="API Key (for OpenAI, DeepSeek, SiliconFlow, etc.)")

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        allowed = {"OLLAMA_LOOPBACK", "OPENAI_COMPAT", "LLAMA_CPP_MANAGED"}
        up = (v or "").strip().upper()
        if up not in allowed:
            raise ValueError(f"provider 必须是 {allowed}")
        return up

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = (v or "").strip()
        if not v or len(v) > 500:
            raise ValueError("base_url 不能为空且不超过 500 字符")
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("base_url 必须以 http:// 或 https:// 开头")
        return v

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        v = (v or "").strip()
        if not v or len(v) > 200:
            raise ValueError("model 不能为空且不超过 200 字符")
        return v


class LLMProbeRequest(BaseModel):
    provider: str = Field(default="LLAMA_CPP_MANAGED")
    base_url: str = Field(default="http://127.0.0.1:28088")
    model: str = Field(default="Qwen3.8-27B-UD-Q4_K_M")
    api_key: str | None = Field(default=None)


class LLMProbeResponse(BaseModel):
    status: str = Field(description="PASS or FAILED")
    message: str = Field(description="Probe diagnostic message")
    model: str = Field(description="Target model name")
    provider: str = Field(description="Provider name")
    available_models: list[str] = Field(default_factory=list, description="Available models returned by endpoint")


class PipelineApplicationAuthorization(BaseModel):
    endpoint: str = Field(description="DRAFT_ONLY 或 APPLY_SELECTED_SECTIONS")
    sections: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def validate_authorization(self) -> "PipelineApplicationAuthorization":
        endpoint = self.endpoint.strip().upper()
        allowed = {"STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS", "SCRIPT_BREAKDOWN"}
        sections = list(dict.fromkeys(item.strip().upper() for item in self.sections))
        if endpoint not in {"DRAFT_ONLY", "APPLY_SELECTED_SECTIONS"}:
            raise ValueError("endpoint 必须是 DRAFT_ONLY 或 APPLY_SELECTED_SECTIONS")
        if endpoint == "DRAFT_ONLY" and sections:
            raise ValueError("DRAFT_ONLY 不能授权应用 sections")
        if endpoint == "APPLY_SELECTED_SECTIONS" and (not sections or any(item not in allowed for item in sections)):
            raise ValueError("自动应用必须明确授权至少一个有效 section")
        self.endpoint = endpoint
        self.sections = sections
        return self


class PipelineProductionAuthorization(BaseModel):
    endpoint: str = Field(
        default="STRUCTURE_ONLY",
        description="STRUCTURE_ONLY 或 WAITING_REVIEW",
    )
    production_mode: str = "BALANCED"
    checkpoint_policy: str = "ON_EXCEPTION"
    tts_enabled: bool = True
    max_parallel_episodes: int = Field(default=1, ge=1, le=8)
    min_free_disk_bytes: int = Field(default=5 * 1024 * 1024 * 1024, ge=1, le=1 << 50)
    max_duration_seconds: int = Field(default=24 * 60 * 60, ge=60, le=365 * 24 * 60 * 60)
    max_new_jobs: int = Field(default=600, ge=1, le=1_000_000)
    max_attempts_total: int = Field(default=1_200, ge=1, le=2_000_000)
    max_output_bytes: int = Field(default=100 * 1024 * 1024 * 1024, ge=1, le=1 << 50)
    max_queued_gpu_jobs: int = Field(default=8, ge=1, le=128)
    dispatch_shots_per_tick: int = Field(default=4, ge=1, le=100)

    @model_validator(mode="after")
    def validate_production_authorization(self) -> "PipelineProductionAuthorization":
        self.endpoint = self.endpoint.strip().upper()
        self.production_mode = self.production_mode.strip().upper()
        self.checkpoint_policy = self.checkpoint_policy.strip().upper()
        if self.endpoint not in {"STRUCTURE_ONLY", "WAITING_REVIEW"}:
            raise ValueError("endpoint 必须是 STRUCTURE_ONLY 或 WAITING_REVIEW")
        if self.production_mode not in {"DRAFT", "BALANCED", "QUALITY"}:
            raise ValueError("production_mode 必须是 DRAFT、BALANCED 或 QUALITY")
        if self.checkpoint_policy not in {
            "AUTO_CONTINUE",
            "AFTER_ASSETS",
            "AFTER_SHOT_PLAN",
            "BEFORE_VIDEO",
            "ON_EXCEPTION",
        }:
            raise ValueError("checkpoint_policy 不受支持")
        return self


class StartPipelineRequest(BaseModel):
    source_document_version_id: str | None = Field(default=None, description="已导入原稿版本ID")
    raw_text: str | None = Field(default=None, description="直接提交的原始文本内容")
    visual_style: str = Field(default="国风仙侠 电影级写实 (Cinematic Realistic)", description="视觉艺术风格")
    target_episode_duration_seconds: int = Field(default=120, ge=30, le=600, description="单集目标成片时长(秒) 30-600")
    voice_preset: str = Field(default="DEFAULT_VOX_CPM2", description="声音风格预设")
    auto_run_rendering: bool = Field(default=False, description="已废弃：是否全自动渲染（忽略）")
    capability_profile_version_id: str | None = Field(default=None, description="选定的本机 LLM 方案版本ID")
    llm_config: LLMConfig | None = Field(default=None, description="选定的 AI 大模型参数配置")
    application_authorization: PipelineApplicationAuthorization = Field(
        default_factory=lambda: PipelineApplicationAuthorization(endpoint="DRAFT_ONLY"),
        description="本次运行的具体应用终点；旧客户端默认只生成草案",
    )
    production_authorization: PipelineProductionAuthorization = Field(
        default_factory=PipelineProductionAuthorization,
        description="结构应用完成后是否继续创建整部待审预览；旧客户端默认只处理结构",
    )

    @model_validator(mode="after")
    def check_production_authorization(self) -> "StartPipelineRequest":
        if self.production_authorization.endpoint == "WAITING_REVIEW":
            if self.application_authorization.endpoint != "APPLY_SELECTED_SECTIONS":
                raise ValueError("继续整部生产前必须授权自动应用故事结构")
            required = {"STORY_PLAN", "ASSET_PROPOSALS"}
            if not required.issubset(set(self.application_authorization.sections)):
                raise ValueError("继续整部生产必须授权 STORY_PLAN 和 ASSET_PROPOSALS")
        return self

    @field_validator("visual_style")
    @classmethod
    def validate_style(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("visual_style 不能为空")
        if len(v) > 200:
            raise ValueError("visual_style 不超过 200 字符")
        return v

    @field_validator("voice_preset")
    @classmethod
    def validate_voice(cls, v: str) -> str:
        v = (v or "").strip()
        if len(v) > 100:
            raise ValueError("voice_preset 不超过 100 字符")
        return v or "DEFAULT_VOX_CPM2"

    @field_validator("raw_text")
    @classmethod
    def validate_raw(cls, v: str | None) -> str | None:
        if v is None:
            return None
        # allow empty string to be treated as None later; but validate length if non-empty
        if len(v) > 500_000:
            raise ValueError("raw_text 不得超过 500000 字符，请上传文件")
        return v

    @model_validator(mode="after")
    def check_source(self) -> "StartPipelineRequest":
        has_src = bool((self.source_document_version_id or "").strip())
        has_raw = bool((self.raw_text or "").strip())
        if not has_src and not has_raw:
            raise ValueError("请提供小说来源：source_document_version_id 或 raw_text（至少 20 字符）至少其一")
        if has_src and has_raw:
            raise ValueError("source_document_version_id 与 raw_text 只能选择一个")
        if has_raw and len((self.raw_text or "").strip()) < 20:
            raise ValueError("raw_text 过短，至少需要 20 个字符")
        return self


class PipelinePreflightRequest(BaseModel):
    source_document_version_id: str | None = None
    raw_text: str | None = Field(default=None, max_length=500_000)
    target_episode_duration_seconds: int = Field(default=120, ge=30, le=600)
    capability_profile_version_id: str | None = None

    @model_validator(mode="after")
    def check_source(self) -> "PipelinePreflightRequest":
        has_source = bool((self.source_document_version_id or "").strip())
        has_text = bool((self.raw_text or "").strip())
        if has_source == has_text:
            raise ValueError("请选择且只选择一个原稿来源")
        if has_text and len((self.raw_text or "").strip()) < 20:
            raise ValueError("raw_text 过短，至少需要 20 个字符")
        return self


class ApplyPipelineRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    expected_impact_sha256: str = Field(min_length=64, max_length=64)
    sections: list[str] = Field(min_length=1, max_length=4)

    @field_validator("sections")
    @classmethod
    def validate_sections(cls, value: list[str]) -> list[str]:
        allowed = {"STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS", "SCRIPT_BREAKDOWN"}
        normalized = list(dict.fromkeys(item.strip().upper() for item in value))
        if not normalized or any(item not in allowed for item in normalized):
            raise ValueError("sections 包含不支持的应用内容")
        return normalized


class PreviewPipelineApplyRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    sections: list[str] = Field(min_length=1, max_length=4)

    @field_validator("sections")
    @classmethod
    def validate_sections(cls, value: list[str]) -> list[str]:
        allowed = {"STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS", "SCRIPT_BREAKDOWN"}
        normalized = list(dict.fromkeys(item.strip().upper() for item in value))
        if not normalized or any(item not in allowed for item in normalized):
            raise ValueError("sections 包含不支持的应用内容")
        return normalized


class PipelineApplyPreviewResponse(BaseModel):
    impact: dict[str, object]
    quality_report: dict[str, object]
    can_apply: bool
    #: PR-05: which draft revision is already applied, and whether THIS revision is
    #: the one that was applied.  A newer revision has an applicable delta.
    apply_watermark: dict[str, object] = Field(default_factory=dict)


class RetryPipelineRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class ContinuePipelineAnalysisRequest(BaseModel):
    """Resume the next bounded batch of an already authorised manuscript range."""

    expected_revision: int = Field(ge=1)
    expected_source_sha256: str = Field(min_length=64, max_length=64)
    expected_next_window_index: int | None = Field(default=None, ge=0)


class PipelineRunResponse(BaseModel):
    """Envelope for a single story-planning run.

    ``run`` is the canonical pipeline run projection. It is intentionally typed as
    a free-form object here: this endpoint returns the same projection the other
    run commands return, and pinning a partial field list would silently drop
    fields the frontend already relies on (``draft``, ``analysis_cursor``,
    ``apply_continuation``, ...). The explicit envelope still gives the operation a
    declared response contract instead of an untyped body.
    """

    run: dict[str, object]
