"""Persist an explicitly selected model library without moving model files."""

import json
import os
import tempfile
from pathlib import Path
from threading import Lock

from local_drama.bootstrap.config_loader import MachineConfig
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.filesystem.path_policy import is_reparse_point

_CONFIG_LOCK = Lock()


def add_model_library(settings: Settings, root_path: str) -> Path:
    candidate = Path(root_path.strip()).expanduser()
    if not candidate.is_absolute():
        raise DomainRuleError("MODEL_SCAN_ROOT_ABSOLUTE_REQUIRED", "请输入本机模型文件夹的完整绝对路径。")
    root = candidate.resolve()
    if not root.is_dir() or is_reparse_point(candidate) or is_reparse_point(root):
        raise DomainRuleError("MODEL_SCAN_ROOT_INVALID", "模型文件夹不存在、不是目录或是链接，请检查路径。")
    if os.environ.get("LOCAL_DRAMA_MODEL_LIBRARY_ROOTS"):
        raise DomainRuleError("MODEL_LIBRARY_ENV_MANAGED", "模型目录由启动环境管理，请先由管理员解除该覆盖再通过页面保存。")
    with _CONFIG_LOCK:
        config_path = settings.config_path
        if config_path is None or not config_path.is_file():
            raise DomainRuleError("MODEL_LIBRARY_CONFIG_REQUIRED", "本机配置文件不存在，无法持久保存模型目录。")
        payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
        runtime = payload.setdefault("runtime", {})
        roots = list(dict.fromkeys([*settings.model_library_roots, root]))
        runtime["model_library_roots"] = [str(item) for item in roots]
        MachineConfig.model_validate(payload)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=config_path.parent, prefix=".model-roots-", suffix=".tmp", delete=False) as target:
                temp_path = Path(target.name)
                json.dump(payload, target, ensure_ascii=False, indent=2)
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
            os.replace(temp_path, config_path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        settings.model_library_roots = tuple(roots)
    return root
