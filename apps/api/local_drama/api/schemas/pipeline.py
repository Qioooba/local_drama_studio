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


class StartPipelineRequest(BaseModel):
    source_document_version_id: str | None = Field(default=None, description="已导入原稿版本ID")
    raw_text: str | None = Field(default=None, description="直接提交的原始文本内容")
    visual_style: str = Field(default="国风仙侠 电影级写实 (Cinematic Realistic)", description="视觉艺术风格")
    target_episode_duration_seconds: int = Field(default=120, ge=30, le=600, description="单集目标成片时长(秒) 30-600")
    voice_preset: str = Field(default="DEFAULT_VOX_CPM2", description="声音风格预设")
    auto_run_rendering: bool = Field(default=False, description="已废弃：是否全自动渲染（忽略）")
    capability_profile_version_id: str | None = Field(default=None, description="选定的本机 LLM 方案版本ID")
    llm_config: LLMConfig | None = Field(default=None, description="选定的 AI 大模型参数配置")

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
    sections: list[str] = Field(min_length=1, max_length=4)

    @field_validator("sections")
    @classmethod
    def validate_sections(cls, value: list[str]) -> list[str]:
        allowed = {"STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS", "SCRIPT_BREAKDOWN"}
        normalized = list(dict.fromkeys(item.strip().upper() for item in value))
        if not normalized or any(item not in allowed for item in normalized):
            raise ValueError("sections 包含不支持的应用内容")
        return normalized


class RetryPipelineRequest(BaseModel):
    expected_revision: int = Field(ge=1)
