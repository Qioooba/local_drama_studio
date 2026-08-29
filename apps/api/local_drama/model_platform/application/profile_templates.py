"""Single V2 Profile-template dispatch boundary for public API routes."""

from __future__ import annotations

import json
from dataclasses import dataclass

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.comfy_workflow_profiles import ComfyWorkflowProfileService
from local_drama.model_platform.application.ollama_text_profiles import OllamaTextProfileService
from local_drama.model_platform.application.profile_publication import ProfilePublicationService
from local_drama.model_platform.application.pytorch_embedding_profiles import PyTorchEmbeddingProfileService


@dataclass(frozen=True, slots=True)
class ProvisionedProfileTemplate:
    profile_version_id: str
    profile_code: str
    created: bool


@dataclass(frozen=True, slots=True)
class ProfileTemplateSmokeResult:
    validation_run_id: str
    profile_version_id: str
    status: str


class ProfileTemplateService:
    """Dispatch only persisted V2 templates; no generic runtime escape hatch."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def provision(self, runtime_model_installation_id: str, capability_code: str, *, workflow_binding_id: str | None = None) -> ProvisionedProfileTemplate:
        runtime_kind = self._offering_runtime_kind(runtime_model_installation_id, capability_code)
        if runtime_kind == "OLLAMA":
            result = OllamaTextProfileService(self.database, self.settings).provision(runtime_model_installation_id, capability_code)
        elif runtime_kind == "PYTORCH_PROCESS":
            result = PyTorchEmbeddingProfileService(self.database, self.settings).provision(runtime_model_installation_id, capability_code)
        elif runtime_kind == "COMFYUI":
            result = ComfyWorkflowProfileService(self.database, self.settings).provision(runtime_model_installation_id, capability_code, workflow_binding_id)
        else:
            raise DomainRuleError("MP_PROFILE_TEMPLATE_UNAVAILABLE", "该运行时/能力没有已安装的 V2 Profile 模板。")
        return ProvisionedProfileTemplate(result.profile_version_id, result.profile_code, result.created)

    def smoke(self, profile_version_id: str) -> ProfileTemplateSmokeResult:
        template = self._profile_template(profile_version_id)
        if template == "ollama.text.profile.v1":
            result = OllamaTextProfileService(self.database, self.settings).smoke(profile_version_id)
        elif template == "pytorch.embedding.qwen3.profile.v1":
            result = PyTorchEmbeddingProfileService(self.database, self.settings).smoke(profile_version_id)
        elif template == "comfy.workflow.profile.v1":
            result = ComfyWorkflowProfileService(self.database, self.settings).smoke(profile_version_id)
        else:
            raise DomainRuleError("MP_PROFILE_SMOKE_IMPLEMENTATION_UNAVAILABLE", "该 Profile 模板没有已安装的真实 smoke 实现。")
        return ProfileTemplateSmokeResult(result.validation_run_id, result.profile_version_id, result.status)

    def publish(self, profile_version_id: str, validation_run_id: str, reason: str) -> None:
        ProfilePublicationService(self.database).publish(profile_version_id, validation_run_id=validation_run_id, reason=reason)

    def _offering_runtime_kind(self, runtime_model_installation_id: str, capability_code: str) -> str:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT runtime.kind FROM mp_runtime_model_installations installation
                   JOIN mp_runtime_installation_versions version ON version.id=installation.runtime_installation_version_id
                   JOIN mp_runtime_installations runtime ON runtime.id=version.runtime_installation_id
                   JOIN mp_capability_offerings offering ON offering.runtime_model_installation_id=installation.id
                   JOIN mp_capability_definitions capability ON capability.id=offering.capability_definition_id
                   WHERE installation.id=? AND capability.code=?""",
                (runtime_model_installation_id, capability_code.strip().upper()),
            ).fetchone()
        if row is None:
            raise DomainRuleError("MP_CAPABILITY_OFFERING_NOT_FOUND", "该已登记模型没有声明请求的能力。")
        return str(row["kind"])

    def _profile_template(self, profile_version_id: str) -> str:
        with self.database.connect() as connection:
            row = connection.execute("SELECT payload_json FROM mp_execution_profile_versions WHERE id=?", (profile_version_id,)).fetchone()
        if row is None:
            raise DomainRuleError("MP_PROFILE_VERSION_NOT_FOUND", "待验证的 V2 ProfileVersion 不存在。")
        try:
            payload = json.loads(str(row["payload_json"]))
        except (TypeError, ValueError) as error:
            raise DomainRuleError("MP_PROFILE_PAYLOAD_INVALID", "Profile payload 不是合法 JSON。") from error
        template = payload.get("template") if isinstance(payload, dict) else None
        if not isinstance(template, str) or not template.strip():
            raise DomainRuleError("MP_PROFILE_TEMPLATE_UNAVAILABLE", "Profile 未声明可执行的 V2 模板。")
        return template
